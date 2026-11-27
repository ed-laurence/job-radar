"""What a run spent at llm-proxy: `GET /v1/usage` with the run's own task token.

llm-proxy publishes no host port, so the probe asks from inside the sandbox network: a one-shot
`docker run` of the writer image with the same hardening as the run, Python's stdlib as the HTTP
client and the token passed by name (`-e LLM_PROXY_TOKEN`, value only in the child env). Any
failure (docker, network, status, body) is `None`: the spend is unknown, never zero.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.adapters.agents.process import Completed, capture
from app.adapters.agents.sandbox_spec import HARDENING_FLAGS, NO_PROXY, SandboxConfig, child_env

USAGE_PATH = "/v1/usage"
USAGE_TIMEOUT_S = 45.0
MAX_USAGE_BYTES = 4096
AUTH_VAR = "LLM_PROXY_TOKEN"
# Runs inside the container: no proxy (llm-proxy is on NO_PROXY), body capped, non-200 fails.
USAGE_SCRIPT = (
    "import os,sys,urllib.request as u\n"
    "r=u.Request(os.environ['USAGE_URL'],"
    "headers={'Authorization':'Bearer '+os.environ['LLM_PROXY_TOKEN']})\n"
    "o=u.build_opener(u.ProxyHandler({}))\n"
    "with o.open(r,timeout=20) as f:\n"
    " sys.exit(0 if f.status==200 and sys.stdout.buffer.write(f.read(4097)) else 1)\n"
)


@dataclass(frozen=True)
class ProxyUsage:
    usd_micro_used: int
    usd_micro_budget: int
    requests: int

    @property
    def used_usd(self) -> float:
        return self.usd_micro_used / 1_000_000


class _UsageBody(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True, frozen=True)

    usd_micro_used: int = Field(ge=0)
    usd_micro_budget: int = Field(ge=0)
    requests: int = Field(ge=0)


def parse_usage(raw: bytes) -> ProxyUsage | None:
    if len(raw) > MAX_USAGE_BYTES:
        return None
    try:
        body = _UsageBody.model_validate_json(raw)
    except ValidationError:
        return None
    return ProxyUsage(body.usd_micro_used, body.usd_micro_budget, body.requests)


class UsageProbe(Protocol):
    async def usage(self, token: str) -> ProxyUsage | None:
        """The task's spend so far, or `None` when llm-proxy can't say."""
        ...


class Capture(Protocol):
    def __call__(
        self, argv: Sequence[str], *, env: Mapping[str, str], cwd: Path | None, timeout_s: float
    ) -> Awaitable[Completed]: ...


class DockerUsageProbe:
    def __init__(
        self,
        config: SandboxConfig,
        cli_env: Mapping[str, str] | None = None,
        *,
        capture: Capture = capture,
    ) -> None:
        self._config = config
        self._cli_env = cli_env
        self._capture = capture

    def __repr__(self) -> str:
        return f"DockerUsageProbe(image={self._config.image!r})"

    def argv(self) -> list[str]:
        cfg = self._config
        argv = [cfg.docker, "run", "--rm", "--network", cfg.network]
        for flag in HARDENING_FLAGS:
            argv.extend(flag)
        argv += ["-e", f"NO_PROXY={NO_PROXY}", "-e", f"no_proxy={NO_PROXY}"]
        argv += ["-e", f"USAGE_URL={cfg.llm_base_url.rstrip('/')}{USAGE_PATH}", "-e", AUTH_VAR]
        argv += ["--entrypoint", "python3", cfg.image, "-c", USAGE_SCRIPT]
        return argv

    async def usage(self, token: str) -> ProxyUsage | None:
        parent = os.environ if self._cli_env is None else self._cli_env
        env = child_env(parent, {AUTH_VAR: token})
        try:
            done = await self._capture(self.argv(), env=env, cwd=None, timeout_s=USAGE_TIMEOUT_S)
        except (OSError, TimeoutError):
            return None
        if done.code != 0:
            return None
        return parse_usage(done.stdout)
