"""CODE_HOST and the GitHub settings (docs/features/github-prs.md "Settings"): `github` needs
GITHUB_REPO as owner/name, the deploy key stays outside the project, prod stays local, and no
error message carries a key path."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr, ValidationError

from app.settings import REPO_ROOT, Settings

GITHUB_OK: dict[str, Any] = {"environment": "test", "code_host": "github", "github_repo": "o/r"}


def _github(**changes: Any) -> Settings:
    return Settings(**{**GITHUB_OK, **changes})


def test_defaults_are_local_with_a_one_minute_poll() -> None:
    settings = Settings(environment="test")
    assert settings.code_host == "local"
    assert settings.github_repo is None
    assert settings.github_deploy_key_path == Path.home() / ".config/jobradar/github_deploy_key"
    assert settings.github_known_hosts_path == Path.home() / ".config/jobradar/github_known_hosts"
    assert settings.pr_sync_interval_s == 60


@pytest.mark.parametrize("repo", ["owner-x/repo.name", "a_b/c-d", "O1/R2"])
def test_github_accepts_owner_name(repo: str) -> None:
    assert _github(github_repo=repo).github_repo == repo


@pytest.mark.parametrize("repo", [None, "", "owner", "a/b/c", "a b/c", "../x", "a/..", "x/y;z"])
def test_github_needs_a_valid_repo(repo: str | None) -> None:
    with pytest.raises(ValidationError, match="CODE_HOST=github needs GITHUB_REPO"):
        _github(github_repo=repo)


def test_local_ignores_the_repo() -> None:
    assert Settings(environment="test", code_host="local").code_host == "local"


@pytest.mark.parametrize("name", ["github_deploy_key_path", "github_known_hosts_path"])
def test_key_files_must_live_outside_the_project(name: str) -> None:
    inside = REPO_ROOT / "keys" / "secret-file-name"
    with pytest.raises(ValidationError) as caught:
        _github(**{name: inside})
    message = caught.value.errors()[0]["msg"]
    assert "outside the project folder" in message
    assert "secret-file-name" not in message


def test_pr_sync_interval_zero_turns_it_off_and_negative_is_refused() -> None:
    assert Settings(environment="test", pr_sync_interval_s=0).pr_sync_interval_s == 0
    with pytest.raises(ValidationError):
        Settings(environment="test", pr_sync_interval_s=-1)


def test_prod_refuses_the_github_code_host() -> None:
    prod: dict[str, Any] = {
        "environment": "prod",
        "auth": "identity_platform",
        "guards": "gcp",
        "llm": "vertex",
        "rate_limit_ip_salt": SecretStr("a-real-salt-from-secret-manager"),
        "idp_project_id": "proj-xxxx",
        "gcp_project": "proj-xxxx",
        "database_url": SecretStr("postgresql://app@127.0.0.1:5432/app"),
        "storage": "gcs",
        "storage_bucket": "uploads-xxxx",
    }
    Settings(**prod)
    with pytest.raises(ValidationError, match="CODE_HOST must be local in prod"):
        _github(**prod)
