"""The one place that reads the environment (ARCH-06). Read once at startup.

Secrets (APP_USERS, the denylist, the IP salt) arrive as env vars or mounted files, which is how
Cloud Run exposes Secret Manager secrets. Nothing here is ever logged.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    TypeAdapter,
    ValidationError,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.domain.identity import Allowlist, AllowlistEntry, AppRole, derive_user_id
from app.domain.money import DEFAULT_USD_TO_AUD

REPO_ROOT = Path(__file__).resolve().parents[3]
# A throwaway default for local runs; the prod validator rejects it.
_LOCAL_SALT = "local-only-salt"
_MIN_AGENT_KEY = 32


class _UserRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(max_length=254)
    role: AppRole
    display_name: str | None = Field(default=None, max_length=100)
    id: uuid.UUID | None = None


class _UserFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    users: list[_UserRecord]


_USERS_ADAPTER: TypeAdapter[list[_UserRecord] | _UserFile] = TypeAdapter(
    list[_UserRecord] | _UserFile
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False, frozen=True)

    environment: Literal["local", "test", "prod"] = "local"
    llm: Literal["replay", "vertex", "record"] = "replay"
    # What replay does with an unrecorded prompt. `error` (tests, CI) fails loudly;
    # `synthesize` (scripts/dev.sh only) answers deterministically so new local data still works.
    llm_replay_miss: Literal["error", "synthesize"] = "error"
    # Where live Claude calls go when LLM=vertex|record. `anthropic` (local dev only, e.g. while
    # Vertex quota is pending) uses the first-party API with ANTHROPIC_API_KEY; prod is Vertex.
    claude_backend: Literal["vertex", "anthropic"] = "vertex"
    anthropic_api_key: SecretStr | None = None
    guards: Literal["fake", "gcp"] = "fake"
    # Research agent web search: the provider's server tool (Claude `web_search_20250305`).
    # Off by default; the agent then answers from the user's own records only.
    research_web_search: bool = False
    auth: Literal["fake", "identity_platform"] = "fake"

    app_users: SecretStr | None = None
    app_users_file: Path | None = None
    denylist: SecretStr | None = None
    denylist_file: Path | None = None

    database_url: SecretStr | None = None
    db_pool_size: int = Field(default=5, ge=1, le=20)

    gcp_project: str | None = None
    vertex_region: str = "global"
    maas_region: str = "us-central1"
    model_armor_location: str = "australia-southeast1"
    model_armor_template: str | None = None
    sdp_location: str = "australia-southeast1"
    sdp_deidentify_template: str | None = None
    sdp_reidentify_template: str | None = None
    sdp_inspect_template: str | None = None
    idp_project_id: str | None = None
    idp_tenant_id: str | None = None

    models_config: Path = REPO_ROOT / "ai" / "models.yaml"
    llm_fixtures_dir: Path = REPO_ROOT / "backend" / "tests" / "fixtures" / "llm"
    openapi_path: Path = REPO_ROOT / "api" / "openapi.yaml"
    seed_dir: Path = REPO_ROOT / "seed"
    # The campaign plan; point CAMPAIGN_CONFIG at a private plan outside the repo.
    campaign_config: Path = REPO_ROOT / "config" / "campaign.yaml"

    rate_limit_ip_salt: SecretStr = SecretStr(_LOCAL_SALT)
    trusted_proxy_hops: int = Field(default=0, ge=0, le=5)
    max_body_bytes: int = Field(default=1_048_576, ge=1024)
    # Local e2e only: multiplies every rate-limit tier (can only loosen, never tighten). Pinned
    # to 1 in prod by the validator below.
    rate_limit_scale: float = Field(default=1.0, ge=1.0, le=20.0)
    # Default monthly LLM budget in AUD (a maintainer's setting overrides it); unset means none.
    llm_budget_month_aud: float | None = Field(default=None, gt=0)
    # Providers bill in USD (`llm_calls.cost_usd`); budgets and spend are shown in AUD.
    usd_to_aud: float = Field(default=DEFAULT_USD_TO_AUD, gt=0)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    # The scan: roles released per user per run, and (local only) the in-process schedule.
    # Prod leaves SCAN_INTERVAL_MINUTES unset and runs the scan from Cloud Scheduler.
    scan_batch_size: int = Field(default=5, ge=1, le=50)
    scan_interval_minutes: int | None = Field(default=None, ge=1, le=1440)
    # Object storage for profile documents: local disk (default) or a GCS bucket.
    storage: Literal["local", "gcs"] = "local"
    storage_bucket: str | None = Field(default=None, max_length=222)
    # Dev-board agent runs. `simulation` (default, and the only choice in prod) or `docker`: the
    # local writer sandbox (`dev.sh --with-agents`) with LocalGitHost publishing branches to a
    # scratch bare repo outside the project. See docs/features/dev-agents.md.
    agent_sandbox: Literal["simulation", "docker"] = "simulation"
    agent_sandbox_image: str = Field(default="jobradar-sandbox-writer:dev", max_length=255)
    agent_sandbox_network: str = Field(default="jobradar_sandbox", max_length=100)
    agent_egress_proxy: str = Field(default="http://egress-proxy:3128", max_length=255)
    agent_llm_base_url: str = Field(default="http://llm-proxy:8080", max_length=255)
    agent_coordinator_url: str = Field(default="http://coordinator:8100", max_length=255)
    agent_remote_path: Path = Field(
        default_factory=lambda: Path.home() / ".cache" / "jobradar" / "agent-remote"
    )
    agent_run_timeout_s: int = Field(default=3600, ge=60, le=23 * 3600)
    # Plan runs under AGENT_SANDBOX=docker: the sandboxed dev-lead (default) or the simulation.
    # A plan run has no plan yet, so its USD budget and wall clock are these settings.
    agent_planner: Literal["sandbox", "simulation"] = "sandbox"
    # llm-proxy holds ~$0.30 per call up front, so $0.50 left the planner only ~8 turns.
    agent_plan_budget_usd: float = Field(default=2.0, gt=0, le=20)
    agent_plan_timeout_s: int = Field(default=600, ge=60, le=7200)
    # Triage under AGENT_SANDBOX=docker: the read-only sandboxed triager (default; it can read
    # the repo and run existing tests) or the in-app LLM triager. Ignored without docker.
    agent_triager: Literal["sandbox", "in_app"] = "sandbox"
    agent_triage_budget_usd: float = Field(default=0.5, gt=0, le=20)
    agent_triage_timeout_s: int = Field(default=300, ge=60, le=7200)
    agent_triage_model: str = Field(default="claude-sonnet-5-5", min_length=1, max_length=100)
    # HMAC key for per-task llm-proxy tokens (shared with llm-proxy only), and the key the
    # coordinator signs task-scoped sandbox tokens with.
    llm_proxy_secret: SecretStr | None = None
    coordinator_token_key: SecretStr | None = None

    @model_validator(mode="after")
    def _agent_sandbox_is_complete(self) -> Self:
        if self.agent_sandbox != "docker":
            return self
        secret = self.llm_proxy_secret
        if secret is None or len(secret.get_secret_value().encode()) < _MIN_AGENT_KEY:
            raise ValueError(
                f"AGENT_SANDBOX=docker needs LLM_PROXY_SECRET (at least {_MIN_AGENT_KEY} bytes)"
            )
        key = self.coordinator_token_key
        if key is None or len(key.get_secret_value().encode()) < _MIN_AGENT_KEY:
            raise ValueError(
                "AGENT_SANDBOX=docker needs COORDINATOR_TOKEN_KEY"
                f" (at least {_MIN_AGENT_KEY} bytes)"
            )
        remote = self.agent_remote_path.expanduser().resolve()
        if remote == REPO_ROOT or REPO_ROOT in remote.parents:
            raise ValueError("AGENT_REMOTE_PATH must be outside the project folder")
        return self

    @model_validator(mode="after")
    def _prod_is_locked_down(self) -> Self:
        if self.environment != "prod":
            return self
        problems: list[str] = []
        if self.auth != "identity_platform":
            problems.append("AUTH must be identity_platform in prod")
        if self.guards != "gcp":
            problems.append("GUARDS must be gcp in prod")
        if self.llm != "vertex":
            problems.append("LLM must be vertex in prod")
        if self.rate_limit_ip_salt.get_secret_value() == _LOCAL_SALT:
            problems.append("RATE_LIMIT_IP_SALT must be set in prod")
        if self.rate_limit_scale != 1.0:
            problems.append("RATE_LIMIT_SCALE must be 1 in prod")
        if self.llm_replay_miss != "error":
            problems.append("LLM_REPLAY_MISS must be error in prod")
        if self.claude_backend != "vertex" or self.anthropic_api_key is not None:
            problems.append("CLAUDE_BACKEND must be vertex in prod, with no ANTHROPIC_API_KEY")
        if self.storage != "gcs" or not self.storage_bucket:
            problems.append("STORAGE must be gcs with STORAGE_BUCKET in prod")
        if self.agent_sandbox != "simulation":
            problems.append(
                "AGENT_SANDBOX must be simulation in prod (Docker and LocalGitHost are local-only)"
            )
        if not self.idp_project_id or not self.gcp_project or self.database_url is None:
            problems.append("IDP_PROJECT_ID, GCP_PROJECT and DATABASE_URL are required in prod")
        if problems:
            raise ValueError("; ".join(problems))
        return self

    def allowlist(self) -> Allowlist:
        """`APP_USERS`: `[{email, role, display_name?, id?}]` or `{"users": [...]}`."""
        raw = _read_secret(self.app_users, self.app_users_file)
        if raw is None:
            return Allowlist([])
        try:
            parsed = _USERS_ADAPTER.validate_json(raw)
        except ValidationError:
            # Re-raise without the parser's detail: it would echo the secret's content.
            raise ValueError("APP_USERS is not a valid user list") from None
        records = parsed.users if isinstance(parsed, _UserFile) else parsed
        return Allowlist(_entry(record) for record in records)

    def denylist_values(self) -> tuple[str, ...]:
        raw = _read_secret(self.denylist, self.denylist_file)
        if raw is None:
            return ()
        lines = (line.strip() for line in raw.splitlines())
        return tuple(line for line in lines if line and not line.startswith("#"))


def _read_secret(value: SecretStr | None, path: Path | None) -> str | None:
    if value is not None:
        return value.get_secret_value()
    if path is not None and path.is_file():
        return path.read_text(encoding="utf-8")
    return None


def _entry(record: _UserRecord) -> AllowlistEntry:
    return AllowlistEntry(
        email=record.email.lower(),
        role=record.role,
        user_id=record.id or derive_user_id(record.email),
        display_name=record.display_name,
    )
