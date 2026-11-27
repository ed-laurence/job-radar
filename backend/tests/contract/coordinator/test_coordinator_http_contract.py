"""The coordinator's HTTP contract (docs/features/coordinator.md): `GET /healthz`, the `/v1`
routes, task-token auth (401/403), strict JSON (422), size caps (413, refused and never cut),
RFC 9457 problems, redacted responses <= 2k tokens, server-side lease waits, and every refusal
for a token scoped to another task or run."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid
from collections.abc import AsyncGenerator, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy.exc import OperationalError
from tests.unit.coordinator.support import BASE_URL, KEY, OWNER, Harness, call, harness

from app.coordinator.schemas import MAX_BODY_BYTES, RESULT_TOKENS
from app.coordinator.server import build_app
from app.domain.devboard.messages import estimate_tokens
from app.domain.devboard.model import Column, RunRecord, RunStatus, StepKind, TaskRecord

HANDOFF: dict[str, object] = {
    "status": "done",
    "summary": "Added the empty state.",
    "changed_paths": ["web/src/features/contacts/Empty.tsx"],
    "tests": {"passed": 2, "failed": 0},
    "rubric_checked": ["ARCH-40"],
    "open_questions": [],
    "skipped_checks": ["none"],
}
# (method, path, a valid body or None)
ROUTES: dict[str, tuple[str, str, dict[str, object] | None]] = {
    "task": ("GET", "/v1/task", None),
    "claim": ("POST", "/v1/leases/claim", {"paths": ["web/src/features/contacts/"], "wait_s": 0}),
    "renew": ("POST", "/v1/leases/renew", {}),
    "release": ("POST", "/v1/leases/release", {}),
    "steps": ("POST", "/v1/steps", {"kind": "message", "text": "Wrote the failing test."}),
    "handoff": ("POST", "/v1/handoff", HANDOFF),
    "help": ("POST", "/v1/help", {"text": "The lease on web/src/lib/ never frees."}),
}
POSTS = sorted(name for name, (method, _, _) in ROUTES.items() if method == "POST")


@dataclass
class Live:
    h: Harness
    http: httpx.AsyncClient
    task: TaskRecord
    run: RunRecord
    token: str

    async def send(self, name: str, body: Mapping[str, object] | None = None) -> httpx.Response:
        method, path, valid = ROUTES[name]
        return await call(self.http, self.token, method, path, body if body is not None else valid)


@asynccontextmanager
async def serving(h: Harness | None = None) -> AsyncGenerator[Live]:
    h = h or harness()
    task = await h.add_task(body="Brief with key AKIAABCDEFGHIJKLMNOP inside.")
    run = await h.add_run(task)
    app = build_app(h.runtime)
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url=BASE_URL) as http,
    ):
        yield Live(h, http, task, run, h.token(task.id, run.id))


def _ok(response: httpx.Response) -> dict[str, Any]:
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/json")
    body: dict[str, Any] = response.json()
    assert estimate_tokens(json.dumps(body)) <= RESULT_TOKENS
    return body


def _problem(response: httpx.Response, status: int) -> dict[str, Any]:
    assert response.status_code == status, response.text
    assert response.headers["content-type"].startswith("application/problem+json")
    body: dict[str, Any] = response.json()
    assert body["status"] == status
    assert str(body["type"]).startswith("/problems/")
    assert {"title", "correlation_id"} <= set(body)
    return body


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


async def _unchanged(live: Live) -> None:
    assert live.h.runs.steps[live.run.id] == []
    assert live.h.leases.leases == {}
    stored = await live.h.tasks.get(OWNER, live.task.id)
    assert stored is not None
    assert stored.column is Column.AGENT_DEV


# ------------------------------------------------------------------ health and transport
async def test_healthz_is_exactly_status_ok_without_a_token() -> None:
    async with serving() as live:
        response = await live.http.get("/healthz")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


async def test_an_unknown_host_header_is_refused() -> None:
    async with serving() as live:
        response = await live.http.get(
            "/v1/task", headers={"Authorization": f"Bearer {live.token}", "Host": "evil.test"}
        )
        assert response.status_code == 400


@pytest.mark.parametrize("name", POSTS)
async def test_an_oversized_body_is_413_and_nothing_is_stored(name: str) -> None:
    async with serving() as live:
        method, path, _ = ROUTES[name]
        content = json.dumps({"text": "x" * (MAX_BODY_BYTES + 1)}).encode()
        response = await call(live.http, live.token, method, path, content=content)
        _problem(response, 413)
        await _unchanged(live)


async def test_an_oversized_chunked_body_without_length_is_413() -> None:
    async with serving() as live:

        async def chunks() -> AsyncGenerator[bytes]:
            for _ in range(MAX_BODY_BYTES // 1024 + 2):
                yield b" " * 1024

        response = await live.http.post(
            "/v1/steps", content=chunks(), headers={"Authorization": f"Bearer {live.token}"}
        )
        _problem(response, 413)


# ------------------------------------------------------------------ authentication
@pytest.mark.parametrize("name", sorted(ROUTES))
async def test_a_missing_token_is_401_with_a_bearer_challenge(name: str) -> None:
    async with serving() as live:
        method, path, body = ROUTES[name]
        response = await call(live.http, None, method, path, body)
        problem = _problem(response, 401)
        assert response.headers["www-authenticate"] == "Bearer"
        assert problem["code"] == "unauthenticated"
        await _unchanged(live)


def _flip_signature(token: str) -> str:
    return token[:-2] + ("BB" if token.endswith("AA") else "AA")


def _other_prefix(token: str) -> str:
    return token.replace("jrc1.", "jrt1.", 1)


def _unsigned(token: str) -> str:
    return token.rsplit(".", 1)[0]


def _garbage(_token: str) -> str:
    return "not-a-token"


@pytest.mark.parametrize("mutate", [_flip_signature, _other_prefix, _unsigned, _garbage])
async def test_tampered_tokens_are_401(mutate: Callable[[str], str]) -> None:
    async with serving() as live:
        token = mutate(live.token)
        response = await call(live.http, token, "GET", "/v1/task")
        _problem(response, 401)
        assert token not in response.text


async def test_an_expired_token_is_401() -> None:
    async with serving() as live:
        live.h.clock.advance(timedelta(seconds=601))
        _problem(await live.send("task"), 401)


async def test_a_signed_token_for_another_audience_is_401() -> None:
    async with serving() as live:
        claims = {
            "t": str(live.task.id),
            "r": str(live.run.id),
            "exp": int(live.h.clock.now().timestamp()) + 60,
            "aud": "llm-proxy",
        }
        head = "jrc1." + _b64(json.dumps(claims).encode())
        token = f"{head}.{_b64(hmac.new(KEY, head.encode(), hashlib.sha256).digest())}"
        _problem(await call(live.http, token, "GET", "/v1/task"), 401)


@pytest.mark.parametrize("name", sorted(ROUTES))
async def test_a_token_pairing_this_task_with_another_tasks_run_is_403(name: str) -> None:
    async with serving() as live:
        other_task = await live.h.add_task()
        other_run = await live.h.add_run(other_task)
        method, path, body = ROUTES[name]
        for task_id, run_id in ((live.task.id, other_run.id), (other_task.id, live.run.id)):
            token = live.h.token(task_id, run_id)
            problem = _problem(await call(live.http, token, method, path, body), 403)
            assert problem["code"] == "forbidden"
        await _unchanged(live)
        assert live.h.runs.steps[other_run.id] == []


@pytest.mark.parametrize("status", [RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.KILLED])
async def test_a_token_for_a_finished_run_is_403(status: RunStatus) -> None:
    async with serving() as live:
        finished = await live.h.add_run(live.task, status=status, parent=live.run.id)
        token = live.h.token(live.task.id, finished.id)
        _problem(await call(live.http, token, "GET", "/v1/task"), 403)


async def test_a_token_for_an_unknown_task_is_403() -> None:
    async with serving() as live:
        token = live.h.token(uuid.uuid4(), live.run.id)
        _problem(await call(live.http, token, "GET", "/v1/task"), 403)


class _BrokenOwners:
    def __init__(self, _tasks: object) -> None:
        pass

    async def owner_of(self, task_id: uuid.UUID) -> uuid.UUID | None:
        statement = f"SELECT secret FROM somewhere WHERE id = '{task_id}'"
        raise OperationalError(statement, {}, Exception("db-host:5432 down"))


async def test_a_database_failure_is_503_without_details() -> None:
    h = harness(owners=_BrokenOwners)
    app = build_app(h.runtime)
    token = h.token(uuid.uuid4(), uuid.uuid4())
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url=BASE_URL) as http,
    ):
        response = await call(http, token, "GET", "/v1/task")
    _problem(response, 503)
    assert "SELECT" not in response.text
    assert "db-host" not in response.text


# ------------------------------------------------------------------ operations
@pytest.mark.parametrize("name", sorted(ROUTES))
async def test_every_operation_works_and_answers_under_the_response_cap(name: str) -> None:
    async with serving() as live:
        _ok(await live.send(name))


async def test_get_task_returns_the_brief_criteria_plan_leases_and_budget() -> None:
    async with serving() as live:
        body = _ok(await live.send("task"))
        assert body["task"]["id"] == str(live.task.id)  # ids are not redacted
        assert body["acceptance_criteria"][0]["test_id"].endswith("::shows")
        assert body["plan"]["approved"] is True
        assert body["lease_paths"] == ["web/src/features/"]
        assert body["budget"] == {"usd": 1.0, "minutes": 30}
        assert body["next_cursor"] is None


async def test_get_task_pages_a_long_brief_under_2k_tokens() -> None:
    h = harness()
    body = " ".join(f"word{index}" for index in range(1400))
    task = await h.add_task(body=body)
    run = await h.add_run(task)
    app = build_app(h.runtime)
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url=BASE_URL) as http,
    ):
        cursor: int | None = 0
        brief = ""
        token = h.token(task.id, run.id)
        while cursor is not None:
            page = _ok(await call(http, token, "GET", f"/v1/task?cursor={cursor}"))
            brief += page["brief"]
            cursor = page["next_cursor"]
    assert brief == body


@pytest.mark.parametrize("query", ["?cursor=-1", "?cursor=x", "?task_id=1", "?cursor=99999"])
async def test_get_task_refuses_bad_queries(query: str) -> None:
    async with serving() as live:
        _problem(await call(live.http, live.token, "GET", f"/v1/task{query}"), 422)


async def test_responses_pass_the_redaction_filter() -> None:
    async with serving() as live:
        response = await live.send("task")
        assert "AKIA" not in response.text
        assert "[redacted]" in _ok(response)["brief"]


@pytest.mark.parametrize("name", POSTS)
async def test_no_operation_accepts_a_task_or_run_id(name: str) -> None:
    async with serving() as live:
        other = await live.h.add_task()
        _, _, valid = ROUTES[name]
        for extra in ({"task_id": str(other.id)}, {"run_id": str(uuid.uuid4())}):
            body = {**(valid or {}), **extra}
            problem = _problem(await live.send(name, body), 422)
            assert problem["code"] == "validation-failed"
        await _unchanged(live)


@pytest.mark.parametrize(
    ("name", "body", "status"),
    [
        ("claim", {"paths": ["web/src/x.ts"], "wait_s": 61}, 422),
        ("claim", {"paths": ["web/src/x.ts"], "wait_s": "5"}, 422),
        ("claim", {"paths": ["web/src/x.ts"], "wait_s": True}, 422),
        ("claim", {"paths": []}, 422),
        ("claim", {"paths": ["web/src/**/*.ts"]}, 422),
        ("claim", {"paths": [f"web/src/f{index}.ts" for index in range(51)]}, 413),
        ("steps", {"kind": "message", "text": "x" * 2001}, 413),
        ("steps", {"kind": "handoff", "text": "pretend handoff"}, 422),
        ("steps", {"kind": "message", "text": ""}, 422),
        ("handoff", {**HANDOFF, "summary": " ".join(["w"] * 81)}, 413),
        ("handoff", {**HANDOFF, "open_questions": ["1", "2", "3", "4"]}, 413),
        ("handoff", {**HANDOFF, "summary": "x " * 900}, 413),
        ("handoff", {**HANDOFF, "status": "finished"}, 422),
        ("handoff", {**HANDOFF, "summary": "Done.\n```python\nprint(1)\n```"}, 422),
        ("help", {"text": " ".join(["help"] * 160)}, 413),
        ("help", {"text": "x" * 2001}, 413),
        ("release", {"paths": []}, 422),
    ],
)
async def test_oversized_messages_are_413_and_malformed_ones_422(
    name: str, body: dict[str, object], status: int
) -> None:
    async with serving() as live:
        problem = _problem(await live.send(name, body), status)
        assert problem["errors"]
        assert "x" * 100 not in json.dumps(problem)  # input is never echoed
        await _unchanged(live)


@pytest.mark.parametrize("name", POSTS)
async def test_a_body_that_is_not_a_json_object_is_422(name: str) -> None:
    async with serving() as live:
        method, path, _ = ROUTES[name]
        response = await call(live.http, live.token, method, path, content=b"[1, 2")
        _problem(response, 422)


async def test_claim_waits_server_side_until_the_other_lease_is_released() -> None:
    async with serving() as live:
        other_task = await live.h.add_task()
        holder = await live.h.add_run(other_task)
        holder_token = live.h.token(other_task.id, holder.id)
        held = await call(
            live.http, holder_token, "POST", "/v1/leases/claim", {"paths": ["web/src/features/"]}
        )
        assert _ok(held)["granted"]

        async def release_after_two_seconds() -> None:
            if sum(live.h.slept) >= 2:
                await call(live.http, holder_token, "POST", "/v1/leases/release", {})

        live.h.on_sleep.append(release_after_two_seconds)
        body = {"paths": ["web/src/features/contacts/"], "wait_s": 60}
        granted = _ok(await live.send("claim", body))
        assert granted["waiting"] == []
        assert granted["granted"][0]["path"] == "web/src/features/contacts/"
        assert granted["waited_s"] == 2


async def test_a_claim_that_times_out_lists_waiting_paths_but_not_the_other_task() -> None:
    async with serving() as live:
        other_task = await live.h.add_task()
        holder = await live.h.add_run(other_task)
        holder_token = live.h.token(other_task.id, holder.id)
        await call(live.http, holder_token, "POST", "/v1/leases/claim", {"paths": ["web/src/lib/"]})
        response = await live.send("claim", {"paths": ["web/src/lib/api.ts"], "wait_s": 3})
        refused = _ok(response)
        assert refused["granted"] == []
        assert refused["waiting"] == ["web/src/lib/"]
        assert refused["waited_s"] == 3
        assert str(other_task.id) not in response.text
        assert str(holder.id) not in response.text


async def test_release_with_some_but_not_all_held_paths_is_refused() -> None:
    async with serving() as live:
        both = {"paths": ["web/src/features/a/", "web/src/features/b/"]}
        _ok(await live.send("claim", both))
        _problem(await live.send("release", {"paths": ["web/src/features/a/"]}), 422)
        assert _ok(await live.send("release", both)) == {"released": 2}


async def test_steps_are_stored_redacted_for_the_board_stream() -> None:
    async with serving() as live:
        body = {"kind": "thought", "text": "Token ghp_" + "a" * 36 + " seen in a log."}
        assert _ok(await live.send("steps", body)) == {"seq": 1}
        (step,) = live.h.runs.steps[live.run.id]
        assert step.kind is StepKind.THOUGHT
        assert "ghp_" not in step.summary


async def test_handoff_is_accepted_and_stored_as_a_handoff_step() -> None:
    async with serving() as live:
        accepted = _ok(await live.send("handoff"))
        assert accepted["accepted"] is True
        (step,) = live.h.runs.steps[live.run.id]
        assert step.kind is StepKind.HANDOFF


async def test_help_moves_the_task_to_blocked() -> None:
    async with serving() as live:
        assert _ok(await live.send("help")) == {"column": "blocked", "seq": 1}
        stored = await live.h.tasks.get(OWNER, live.task.id)
        assert stored is not None
        assert stored.column is Column.BLOCKED
