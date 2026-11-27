"""Prod refuses every local-only convenience (fake auth/guards/LLM, synthetic replay, loosened
rate limits, the local salt). Each rule is checked by breaking exactly one setting."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import SecretStr, ValidationError

from app.domain.rate_limits import BucketScope, RateTier, policy_for
from app.settings import Settings

PROD_OK: dict[str, Any] = {
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


def test_valid_prod_settings_are_accepted() -> None:
    Settings(**PROD_OK)


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"auth": "fake"}, "AUTH must be identity_platform"),
        ({"guards": "fake"}, "GUARDS must be gcp"),
        ({"llm": "replay"}, "LLM must be vertex"),
        ({"rate_limit_ip_salt": SecretStr("local-only-salt")}, "RATE_LIMIT_IP_SALT must be set"),
        ({"rate_limit_scale": 10.0}, "RATE_LIMIT_SCALE must be 1"),
        ({"llm_replay_miss": "synthesize"}, "LLM_REPLAY_MISS must be error"),
        ({"database_url": None}, "DATABASE_URL are required"),
        ({"claude_backend": "anthropic"}, "CLAUDE_BACKEND must be vertex"),
        ({"anthropic_api_key": SecretStr("sk-test-not-real")}, "no ANTHROPIC_API_KEY"),
        ({"storage": "local"}, "STORAGE must be gcs"),
        ({"storage_bucket": None}, "STORAGE must be gcs"),
    ],
)
def test_each_local_only_setting_is_refused_in_prod(override: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError) as caught:
        Settings(**{**PROD_OK, **override})
    assert message in str(caught.value)


def test_rate_limit_scale_can_only_loosen() -> None:
    with pytest.raises(ValidationError):
        Settings(rate_limit_scale=0.5)


def test_scale_multiplies_capacity_and_refill() -> None:
    base = policy_for(BucketScope.USER, RateTier.DEFAULT)
    scaled = policy_for(BucketScope.USER, RateTier.DEFAULT, scale=10.0)
    assert (scaled.capacity, scaled.refill_per_minute) == (
        base.capacity * 10,
        base.refill_per_minute * 10,
    )
