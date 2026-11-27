"""`python -m app.coordinator.mint --task <id> --run <id> --ttl <s>` prints a token."""

from __future__ import annotations

import io
import time
import uuid

from pydantic import SecretStr

from app.coordinator.config import CoordinatorSettings
from app.coordinator.mint import main
from app.coordinator.tokens import verify

KEY = "m" * 40
TASK = uuid.UUID("00000000-0000-4000-8000-0000000000a1")
RUN = uuid.UUID("00000000-0000-4000-8000-0000000000b1")


def _settings(key: str | None = KEY) -> CoordinatorSettings:
    secret = None if key is None else SecretStr(key)
    return CoordinatorSettings(environment="test", coordinator_token_key=secret)


def test_prints_a_token_for_the_task_and_run() -> None:
    out, err = io.StringIO(), io.StringIO()
    argv = ["--task", str(TASK), "--run", str(RUN), "--ttl", "120"]
    assert main(argv, settings=_settings(), out=out, err=err) == 0
    token = out.getvalue().strip()
    grant = verify(KEY, token, now=int(time.time()))
    assert (grant.task_id, grant.run_id) == (TASK, RUN)
    assert err.getvalue() == ""


def test_without_a_key_it_fails_without_printing_a_token() -> None:
    out, err = io.StringIO(), io.StringIO()
    argv = ["--task", str(TASK), "--run", str(RUN), "--ttl", "120"]
    assert main(argv, settings=_settings(None), out=out, err=err) == 2
    assert out.getvalue() == ""
    assert "COORDINATOR_TOKEN_KEY" in err.getvalue()


def test_refuses_a_ttl_over_24_hours_and_bad_ids() -> None:
    for argv in (
        ["--task", str(TASK), "--run", str(RUN), "--ttl", "86401"],
        ["--task", "nope", "--run", str(RUN), "--ttl", "60"],
    ):
        out, err = io.StringIO(), io.StringIO()
        assert main(argv, settings=_settings(), out=out, err=err) == 2
        assert out.getvalue() == ""
        assert KEY not in err.getvalue()
