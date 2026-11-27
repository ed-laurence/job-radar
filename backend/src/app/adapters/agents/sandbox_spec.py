"""The writer sandbox's `docker run` contract, shared by the run and the usage probe.

Mirrors `sandbox/run.sh` exactly: hardening flags and the fixed env values. The parity test
(`tests/unit/agents_runner/test_run_sh_parity.py`) parses run.sh and fails on any drift.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

SANDBOX_UID = "10001"
# The container's own tmpfs mount point (not a host temp dir).
CONTAINER_TMP = str(PurePosixPath("/") / "tmp")
HARDENING_FLAGS: tuple[tuple[str, ...], ...] = (
    ("--read-only",),
    ("--tmpfs", f"{CONTAINER_TMP}:rw,nosuid,nodev,size=2g"),
    ("--tmpfs", "/workspace:rw,exec,nosuid,nodev,size=4g,uid=10001,gid=10001,mode=0700"),
    ("--cap-drop", "ALL"),
    ("--security-opt", "no-new-privileges"),
    ("--user", f"{SANDBOX_UID}:{SANDBOX_UID}"),
    ("--pids-limit", "1024"),
    ("--memory", "6g"),
    ("--cpus", "2"),
)
# Both internal services are reached directly on the sandbox network, never through squid.
NO_PROXY = "localhost,127.0.0.1,llm-proxy,coordinator"
# What the docker CLI itself needs from the parent env; nothing else is inherited.
DOCKER_CLI_ENV = (
    "PATH",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "HOME",
    "USERPROFILE",
    "APPDATA",
    "LOCALAPPDATA",
    "PROGRAMDATA",
    "DOCKER_HOST",
    "DOCKER_CONTEXT",
    "DOCKER_CONFIG",
    "DOCKER_CERT_PATH",
    "DOCKER_TLS_VERIFY",
    "XDG_RUNTIME_DIR",
)


@dataclass(frozen=True)
class SandboxConfig:
    image: str
    network: str = "jobradar_sandbox"
    egress_proxy: str = "http://egress-proxy:3128"
    llm_base_url: str = "http://llm-proxy:8080"
    coordinator_url: str = "http://coordinator:8100"
    run_timeout_s: int = 3600
    docker: str = "docker"
    poll_s: float = 1.0
    work_root: Path | None = None
    # `.artifacts/agent-runs/` of the repo; None keeps nothing.
    artifacts_root: Path | None = None
    keep_runs: int = 20


def child_env(parent: Mapping[str, str], secrets: Mapping[str, str]) -> dict[str, str]:
    env = {name: parent[name] for name in DOCKER_CLI_ENV if name in parent}
    env.update(secrets)
    return env
