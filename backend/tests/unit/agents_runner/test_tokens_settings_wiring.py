"""The llm-proxy token mint, the coordinator-token shim, the agent settings (prod refuses the
local sandbox) and the compose switch between the simulation and the Docker runner."""

from __future__ import annotations

import asyncio
import base64
import dataclasses
import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr, ValidationError

from app.adapters.agents.docker_runner import DockerAgentRunner
from app.adapters.agents.tokens import (
    CoordinatorTokenMint,
    CoordinatorTokensUnavailable,
    mint_llm_proxy_token,
)
from app.compose.devboard import agent_runner
from app.coordinator import tokens as coordinator_tokens
from app.domain.devboard.simulation import SimulatedAgentRunner
from app.main import Container
from app.settings import REPO_ROOT, Settings
from tests.unit.agents_runner.fakes import PROXY_SECRET
from tests.unit.test_settings_prod_lockdown import PROD_OK

SECRET = SecretStr(PROXY_SECRET.decode())
COORDINATOR_KEY = SecretStr("coordinator-key-test-only-0123456789")
DOCKER: dict[str, Any] = {
    "agent_sandbox": "docker",
    "llm_proxy_secret": SECRET,
    "coordinator_token_key": COORDINATOR_KEY,
}


def docker_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {"environment": "test", **DOCKER, **overrides}
    return Settings(**values)


def _claims(token: str) -> dict[str, object]:
    payload = token.split(".")[1]
    claims: dict[str, object] = json.loads(base64.urlsafe_b64decode(payload + "=" * 4))
    return claims


def test_mint_matches_the_documented_format() -> None:
    token = mint_llm_proxy_token(PROXY_SECRET, "task-1", 60, 1000, now=100.0)
    assert token.startswith("jrt1.")
    assert token.count(".") == 2
    assert "=" not in token
    assert _claims(token) == {"b": 1000, "exp": 160, "t": "task-1"}


@pytest.mark.parametrize(
    ("secret", "task_id", "ttl", "budget"),
    [
        (b"short", "task-1", 60, 1000),
        (PROXY_SECRET, "bad id!", 60, 1000),
        (PROXY_SECRET, "task-1", 25 * 3600, 1000),
        (PROXY_SECRET, "task-1", -1, 1000),
        (PROXY_SECRET, "task-1", 60, 0),
    ],
)
def test_mint_refuses_bad_input(secret: bytes, task_id: str, ttl: int, budget: int) -> None:
    with pytest.raises(ValueError, match=r".+") as caught:
        mint_llm_proxy_token(secret, task_id, ttl, budget)
    assert PROXY_SECRET.decode() not in str(caught.value)


def test_coordinator_token_verifies_with_the_coordinator_key() -> None:
    task, run = uuid.uuid4(), uuid.uuid4()
    mint = CoordinatorTokenMint(COORDINATOR_KEY.get_secret_value().encode(), wall=lambda: 1000.0)
    token = mint.mint(task, run, 600)
    grant = coordinator_tokens.verify(COORDINATOR_KEY.get_secret_value(), token, now=1001)
    assert (grant.task_id, grant.run_id, grant.expires_at) == (task, run, 1600)
    assert COORDINATOR_KEY.get_secret_value() not in repr(mint)


def test_coordinator_mint_refusal_is_typed_and_quiet() -> None:
    with pytest.raises(CoordinatorTokensUnavailable) as caught:
        CoordinatorTokenMint(b"short-key").mint(uuid.uuid4(), uuid.uuid4(), 60)
    assert "short-key" not in str(caught.value)


# ------------------------------------------------------------------ settings
def test_simulation_is_the_default() -> None:
    settings = Settings(environment="test")
    assert settings.agent_sandbox == "simulation"
    assert settings.agent_remote_path == Path.home() / ".cache" / "jobradar" / "agent-remote"
    assert settings.agent_sandbox_image == "jobradar-sandbox-writer:dev"


def test_docker_needs_the_llm_proxy_secret() -> None:
    with pytest.raises(ValidationError, match="LLM_PROXY_SECRET"):
        docker_settings(llm_proxy_secret=None)
    with pytest.raises(ValidationError, match="LLM_PROXY_SECRET"):
        docker_settings(llm_proxy_secret=SecretStr("short"))
    docker_settings()


def test_docker_needs_the_coordinator_token_key() -> None:
    with pytest.raises(ValidationError, match="COORDINATOR_TOKEN_KEY"):
        docker_settings(coordinator_token_key=None)
    with pytest.raises(ValidationError, match="COORDINATOR_TOKEN_KEY"):
        docker_settings(coordinator_token_key=SecretStr("short"))


def test_agent_remote_must_live_outside_the_project() -> None:
    with pytest.raises(ValidationError, match="AGENT_REMOTE_PATH"):
        docker_settings(agent_remote_path=REPO_ROOT / ".cache" / "remote")


def test_prod_refuses_the_local_sandbox() -> None:
    with pytest.raises(ValidationError, match="AGENT_SANDBOX must be simulation in prod"):
        docker_settings(**PROD_OK)


def test_secrets_are_not_in_the_settings_repr() -> None:
    settings = docker_settings()
    assert PROXY_SECRET.decode() not in repr(settings)
    assert "coordinator-key-test-only" not in repr(settings)


# ------------------------------------------------------------------ compose
def test_compose_uses_the_simulation_by_default(container: Container) -> None:
    runner = agent_runner(container, asyncio.sleep, 0.0)
    assert isinstance(runner, SimulatedAgentRunner)


def test_compose_uses_docker_when_asked(container: Container, tmp_path: Path) -> None:
    settings = docker_settings(agent_remote_path=tmp_path / "remote")
    runner = agent_runner(dataclasses.replace(container, settings=settings), asyncio.sleep, 0.0)
    assert isinstance(runner, DockerAgentRunner)
    assert not (tmp_path / "remote").exists(), "building the runner must not touch disk"
