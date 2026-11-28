"""The coordinator HTTP client against an in-process fake API (no network)."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import coordinator_client as cc
import pytest

TOKEN = "jrc1.fake-payload.fake-signature"
PAGES = [
    {
        "task": {"id": "T-7", "title": "Card badge"},
        "brief": "Add a ",
        "acceptance": [1],
        "next_cursor": 1,
        "total_items": 3,
    },
    {
        "task": {"id": "IGNORED"},
        "brief": "badge.",
        "acceptance": [2],
        "next_cursor": None,
        "total_items": 3,
    },
]


@dataclass
class Call:
    method: str
    path: str
    query: dict[str, list[str]]
    headers: dict[str, str]
    body: object


@dataclass
class FakeApi:
    calls: list[Call] = field(default_factory=list[Call])
    status: int = 200
    reply: object = field(default_factory=lambda: {"ok": True})
    problem: dict[str, object] | None = None


@pytest.fixture
def api() -> Iterator[tuple[FakeApi, str]]:
    state = FakeApi()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            return

        def _handle(self) -> None:
            url = urlsplit(self.path)
            length = int(self.headers.get("content-length", "0"))
            raw = self.rfile.read(length) if length else b""
            state.calls.append(
                Call(
                    self.command,
                    url.path,
                    parse_qs(url.query),
                    {k.lower(): v for k, v in self.headers.items()},
                    json.loads(raw) if raw else None,
                )
            )
            if self.headers.get("authorization") != f"Bearer {TOKEN}":
                self._send(401, {"type": "about:blank", "title": "Unauthorized"}, problem=True)
            elif state.problem is not None:
                self._send(state.status, state.problem, problem=True)
            elif url.path == "/v1/task":
                cursor = int(parse_qs(url.query).get("cursor", ["0"])[0])
                self._send(200, PAGES[cursor])
            else:
                self._send(state.status, state.reply)

        def _send(self, status: int, body: object, problem: bool = False) -> None:
            data = json.dumps(body).encode()
            self.send_response(status)
            ctype = "application/problem+json" if problem else "application/json"
            self.send_header("content-type", ctype)
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_GET = _handle
        do_POST = _handle

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield state, f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def _client(url: str, token: str = TOKEN) -> cc.CoordinatorClient:
    return cc.CoordinatorClient(url, token, timeout=5)


def test_get_task_merges_all_pages(api: tuple[FakeApi, str]) -> None:
    state, url = api
    task = _client(url).get_task()
    assert task == {
        "task": {"id": "T-7", "title": "Card badge"},
        "brief": "Add a badge.",
        "acceptance": [1, 2],
        "brief_complete": True,
    }
    assert [(c.method, c.path, c.query) for c in state.calls] == [
        ("GET", "/v1/task", {}),
        ("GET", "/v1/task", {"cursor": ["1"]}),
    ]
    assert state.calls[0].headers["authorization"] == f"Bearer {TOKEN}"


def test_operations_hit_the_contract_paths(api: tuple[FakeApi, str]) -> None:
    state, url = api
    client = _client(url)
    client.claim(["web/src/a.ts", "web/src/b/"], wait_s=90)
    client.renew()
    client.release()
    client.release(["web/src/a.ts"])
    client.step("message", "tests written")
    client.handoff({"status": "done", "summary": "s"})
    client.help("lease wait timed out")
    assert [(c.method, c.path, c.body) for c in state.calls] == [
        ("POST", "/v1/leases/claim", {"paths": ["web/src/a.ts", "web/src/b/"], "wait_s": 60}),
        ("POST", "/v1/leases/renew", {}),
        ("POST", "/v1/leases/release", {}),
        ("POST", "/v1/leases/release", {"paths": ["web/src/a.ts"]}),
        ("POST", "/v1/steps", {"kind": "message", "text": "tests written"}),
        # The handoff object is the body itself (docs/features/coordinator.md), not wrapped.
        ("POST", "/v1/handoff", {"status": "done", "summary": "s"}),
        ("POST", "/v1/help", {"text": "lease wait timed out"}),
    ]
    assert all(c.headers["content-type"] == "application/json" for c in state.calls)


def test_problem_details_and_413(api: tuple[FakeApi, str]) -> None:
    state, url = api
    state.status, state.problem = 422, {"title": "Invalid input", "detail": "kind: bad"}
    with pytest.raises(cc.CoordinatorError) as info:
        _client(url).step("message", "x")
    assert info.value.status == 422
    assert "Invalid input" in str(info.value) and "kind: bad" in str(info.value)
    state.status, state.problem = 413, {"title": "Payload Too Large"}
    with pytest.raises(cc.CoordinatorError) as info:
        _client(url).handoff({"summary": "w " * 500})
    assert info.value.status == 413 and "refused" in str(info.value)


# Regression: a dev agent's handoff got "422 AgentHandoff refused" with no hint of the field,
# so it couldn't fix it. The server's field errors (pointer + message, never the input) pass on.
def test_422_field_errors_reach_the_agent(api: tuple[FakeApi, str]) -> None:
    state, url = api
    errors = [
        {"pointer": "/tests/skipped", "message": "Extra inputs are not permitted"},
        {"pointer": "/status", "message": "Input should be 'done' or 'partial'"},
    ]
    state.status, state.problem = 422, {"title": "AgentHandoff refused", "errors": errors}
    with pytest.raises(cc.CoordinatorError) as info:
        _client(url).handoff({"status": "complete"})
    text = str(info.value)
    assert "/tests/skipped: Extra inputs are not permitted" in text
    assert "/status: Input should be" in text


def test_field_errors_are_capped(api: tuple[FakeApi, str]) -> None:
    state, url = api
    errors = [{"pointer": f"/x{i}", "message": "m" * 500} for i in range(20)]
    state.status, state.problem = 422, {"title": "Invalid input", "errors": errors}
    with pytest.raises(cc.CoordinatorError) as info:
        _client(url).step("message", "x")
    assert "/x4" in str(info.value) and "/x5" not in str(info.value)
    assert len(str(info.value)) < 1500


def test_bad_token_error_does_not_leak_it(api: tuple[FakeApi, str]) -> None:
    _, url = api
    with pytest.raises(cc.CoordinatorError) as info:
        _client(url, "jrc1.wrong.token").get_task()
    assert info.value.status == 401 and "jrc1" not in str(info.value)


def test_unreachable_and_bad_config() -> None:
    with pytest.raises(cc.CoordinatorError):
        cc.CoordinatorClient("http://127.0.0.1:9", TOKEN, timeout=2).renew()
    with pytest.raises(cc.CoordinatorError):
        cc.CoordinatorClient("file:///etc/passwd", TOKEN)
    with pytest.raises(cc.CoordinatorError):
        cc.CoordinatorClient("http://coordinator:8100", "")


def test_granted_paths() -> None:
    result: dict[str, object] = {
        "granted": [{"path": "web/src/a.ts", "kind": "exclusive"}, {"path": ""}, "api/x/"],
        "timed_out": False,
    }
    assert cc.granted_paths(result) == ["web/src/a.ts", "api/x/"]
    assert cc.granted_paths({"granted": [], "timed_out": True}) == []
