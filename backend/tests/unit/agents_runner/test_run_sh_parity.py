"""Drift test: the runner's `docker run` must be `sandbox/run.sh`'s invocation, env and all.

`sandbox/run.sh` is parsed (not grepped): its fixed docker flags, its `env_args` (names and the
values they resolve to with run.sh's own defaults) and its pass-through names. The runner's argv
must carry exactly those flags and every fixed env value, and nothing run.sh would not pass.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass

from app.adapters.agents.docker_runner import DockerAgentRunner, SandboxConfig, SandboxDeps
from app.settings import REPO_ROOT
from tests.unit.agents_runner.fakes import (
    PROXY_SECRET,
    FakeCodeHost,
    FakeCoordinatorTokens,
    FakePlanner,
    FakeProcessRunner,
    FakeTools,
    FakeUsageProbe,
    context,
)

RUN_SH = REPO_ROOT / "sandbox" / "run.sh"
# Flags that take no value in `docker run`.
BOOLEAN_FLAGS = {"--rm", "--read-only"}
# Per-run flags whose values differ by design (name, network, bind mounts) and are checked by
# the other runner tests; their presence is still compared here.
PER_RUN_FLAGS = {"--name", "--network", "--mount"}


@dataclass(frozen=True)
class Invocation:
    flags: list[tuple[str, str | None]]
    fixed_env: dict[str, str]
    runtime_env: set[str]
    passthrough: set[str]


def _logical_lines(text: str) -> list[str]:
    return text.replace("\\\n", " ").splitlines()


def _defaults(lines: list[str]) -> dict[str, str]:
    """`NAME="${VAR:-default}"` assignments, nested defaults resolved to the innermost one."""
    found: dict[str, str] = {}
    for line in lines:
        match = re.fullmatch(r'([A-Z_]+)="(.*)"', line.strip())
        if match is None:
            continue
        value = match.group(2)
        while (inner := re.fullmatch(r"\$\{[A-Za-z_]+:-(.*)\}", value)) is not None:
            value = inner.group(1)
        if "$" not in value:
            found[match.group(1)] = value
    return found


def _flags(tokens: list[str]) -> list[tuple[str, str | None]]:
    flags: list[tuple[str, str | None]] = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if not token.startswith("--"):
            break
        if token in BOOLEAN_FLAGS:
            flags.append((token, None))
            i += 1
        else:
            flags.append((token, tokens[i + 1]))
            i += 2
    return flags


def parse_run_sh() -> Invocation:
    lines = _logical_lines(RUN_SH.read_text(encoding="utf-8"))
    defaults = _defaults(lines)
    text = "\n".join(lines)

    env_block = re.search(r"env_args=\((.*?)\)\n", text, re.S)
    assert env_block is not None, "run.sh: env_args=(...) not found"
    env_tokens = shlex.split(env_block.group(1))
    fixed: dict[str, str] = {}
    runtime: set[str] = set()
    for flag, value in zip(env_tokens[::2], env_tokens[1::2], strict=True):
        assert flag == "-e", f"run.sh env_args: unexpected {flag}"
        name, _, raw = value.partition("=")
        var = re.fullmatch(r"\$\{?([A-Z_]+)\}?", raw)
        if var is None:
            fixed[name] = raw
        elif var.group(1) in defaults:
            fixed[name] = defaults[var.group(1)]
        else:
            runtime.add(name)

    loop = re.search(r"for name in (.*?); do", text, re.S)
    assert loop is not None, "run.sh: pass-through loop not found"
    passthrough = set(loop.group(1).split())

    exec_line = next(line for line in lines if line.strip().startswith("exec docker run"))
    tokens = shlex.split(exec_line)[3:]
    return Invocation(_flags(tokens), fixed, runtime, passthrough)


def parse_runner_argv(argv: list[str], image: str) -> Invocation:
    assert argv[:2] == ["docker", "run"]
    body = argv[2 : argv.index(image)]
    env_values = [body[i + 1] for i, arg in enumerate(body) if arg == "-e"]
    flag_tokens = [tok for i, tok in enumerate(body) if tok != "-e" and body[i - 1] != "-e"]
    fixed: dict[str, str] = {}
    by_name: set[str] = set()
    for value in env_values:
        name, sep, raw = value.partition("=")
        if sep:
            fixed[name] = raw
        else:
            by_name.add(name)
    return Invocation(_flags(flag_tokens), fixed, set(), by_name)


async def _runner_argv() -> tuple[list[str], str]:
    processes = FakeProcessRunner()
    config = SandboxConfig(image="jobradar-sandbox-writer:dev", poll_s=0.01)
    deps = SandboxDeps(
        host=FakeCodeHost(),
        processes=processes,
        coordinator_tokens=FakeCoordinatorTokens(),
        planner=FakePlanner(),
        usage=FakeUsageProbe(),
    )
    await DockerAgentRunner(config, deps, PROXY_SECRET).run(context(), FakeTools())
    return processes.argv, config.image


def test_run_sh_parser_sees_the_reference_invocation() -> None:
    reference = parse_run_sh()
    assert ("--read-only", None) in reference.flags
    assert {"NO_PROXY", "no_proxy", "HTTPS_PROXY", "ANTHROPIC_BASE_URL"} <= set(reference.fixed_env)
    assert "TASK_ID" in reference.runtime_env
    assert {"ANTHROPIC_AUTH_TOKEN", "COORDINATOR_TOKEN"} <= reference.passthrough


def drift(reference: Invocation, ours: Invocation) -> list[str]:
    """Every way the runner's invocation differs from run.sh's."""
    problems: list[str] = []

    def fixed(flags: list[tuple[str, str | None]]) -> list[tuple[str, str | None]]:
        return [flag for flag in flags if flag[0] not in PER_RUN_FLAGS]

    def per_run(flags: list[tuple[str, str | None]]) -> list[str]:
        return [name for name, _ in flags if name in PER_RUN_FLAGS]

    if fixed(ours.flags) != fixed(reference.flags):
        problems.append(f"hardening flags: {fixed(ours.flags)} != {fixed(reference.flags)}")
    if per_run(ours.flags) != per_run(reference.flags):
        problems.append(f"per-run flags: {per_run(ours.flags)} != {per_run(reference.flags)}")
    for name, value in reference.fixed_env.items():
        if ours.fixed_env.get(name) != value:
            problems.append(f"{name}={ours.fixed_env.get(name)!r}, run.sh has {value!r}")
    allowed = set(reference.fixed_env) | reference.runtime_env | reference.passthrough
    names = set(ours.fixed_env) | ours.passthrough
    problems += [f"{name} is not passed by run.sh" for name in sorted(names - allowed)]
    missing = (set(reference.fixed_env) | reference.runtime_env) - names
    problems += [f"{name} is missing" for name in sorted(missing)]
    secrets = {"ANTHROPIC_AUTH_TOKEN", "COORDINATOR_TOKEN"}
    problems += [f"{name} must go by name" for name in sorted(secrets - ours.passthrough)]
    return problems


async def test_runner_invocation_matches_run_sh_exactly() -> None:
    argv, image = await _runner_argv()
    assert drift(parse_run_sh(), parse_runner_argv(argv, image)) == []


async def test_drift_check_catches_the_no_proxy_that_shipped() -> None:
    """The failure that shipped: coordinator missing from NO_PROXY, so its traffic hit squid."""
    argv, image = await _runner_argv()
    old = "localhost,127.0.0.1,llm-proxy"
    doctored = [
        f"{arg.partition('=')[0]}={old}" if arg.lower().startswith("no_proxy=") else arg
        for arg in argv
    ]
    problems = drift(parse_run_sh(), parse_runner_argv(doctored, image))
    assert any(p.startswith("NO_PROXY=") for p in problems)
    assert any(p.startswith("no_proxy=") for p in problems)


async def test_drift_check_catches_a_lost_hardening_flag_and_extra_env() -> None:
    argv, image = await _runner_argv()
    doctored = [arg for arg in argv if arg != "--read-only"]
    at = doctored.index(image)
    doctored[at:at] = ["-e", "SMUGGLED=1"]
    problems = drift(parse_run_sh(), parse_runner_argv(doctored, image))
    assert any(p.startswith("hardening flags") for p in problems)
    assert "SMUGGLED is not passed by run.sh" in problems
