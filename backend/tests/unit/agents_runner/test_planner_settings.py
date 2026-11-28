"""AGENT_PLANNER: with AGENT_SANDBOX=docker, plan runs go to the sandboxed dev-lead by default,
or stay on the simulation when asked; the plan run's budget and wall clock are settings."""

from __future__ import annotations

import asyncio
import dataclasses
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.adapters.agents.docker_runner import DockerAgentRunner
from app.compose.devboard import agent_runner
from app.domain.devboard.model import RunKind
from app.main import Container
from app.settings import Settings
from tests.unit.agents_runner.test_tokens_settings_wiring import docker_settings


def test_plan_defaults() -> None:
    settings = Settings(environment="test")
    assert settings.agent_planner == "sandbox"
    assert settings.agent_plan_budget_usd == 2.0
    assert settings.agent_plan_timeout_s == 600


@pytest.mark.parametrize(
    "overrides",
    [
        {"agent_planner": "cloud"},
        {"agent_plan_budget_usd": 0},
        {"agent_plan_budget_usd": 21},
        {"agent_plan_timeout_s": 30},
        {"agent_plan_timeout_s": 7201},
    ],
)
def test_plan_settings_are_bounded(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate({"environment": "test", **overrides})


def _runner(container: Container, tmp_path: Path, **overrides: object) -> DockerAgentRunner:
    settings = docker_settings(agent_remote_path=tmp_path / "remote", **overrides)
    runner = agent_runner(dataclasses.replace(container, settings=settings), asyncio.sleep, 0.0)
    assert isinstance(runner, DockerAgentRunner)
    return runner


def test_docker_plans_in_the_sandbox_by_default(container: Container, tmp_path: Path) -> None:
    runner = _runner(container, tmp_path, agent_plan_budget_usd=1.25, agent_plan_timeout_s=900)
    assert runner.labels[RunKind.PLAN] == "dev-lead"
    config = vars(runner)["_config"]
    assert (config.plan_budget_usd, config.plan_timeout_s) == (1.25, 900)


def test_the_simulation_planner_can_be_kept(container: Container, tmp_path: Path) -> None:
    runner = _runner(container, tmp_path, agent_planner="simulation")
    assert RunKind.PLAN not in runner.labels
