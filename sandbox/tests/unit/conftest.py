"""Shared fixtures: an in-process llm-proxy with a stub upstream (no network)."""

from __future__ import annotations

import http.client
import json
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field

import pytest
from llm_proxy.budget import BudgetLedger
from llm_proxy.server import Config, make_server
from llm_proxy.tokens import mint

SECRET = b"test-secret-0123456789abcdef0123456789"
API_KEY = "fake-upstream-key-not-real-000000"


@dataclass
class StubResponse:
    status: int
    headers: list[tuple[str, str]]
    body_chunks: list[bytes]
    closed: bool = False

    def chunks(self) -> Iterator[bytes]:
        yield from self.body_chunks

    def close(self) -> None:
        self.closed = True


@dataclass
class StubUpstream:
    response: StubResponse | None = None
    error: Exception | None = None
    calls: list[tuple[str, str, dict[str, str], bytes]] = field(
        default_factory=list[tuple[str, str, dict[str, str], bytes]]
    )

    def send(self, method: str, path: str, headers: dict[str, str], body: bytes) -> StubResponse:
        self.calls.append((method, path, dict(headers), body))
        if self.error:
            raise self.error
        assert self.response is not None
        return self.response


def message_response(input_tokens: int = 10, output_tokens: int = 5) -> StubResponse:
    body = {
        "id": "msg_1",
        "type": "message",
        "content": [{"type": "text", "text": "hi"}],
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    }
    return StubResponse(
        200,
        [("content-type", "application/json"), ("request-id", "req_1"), ("x-internal", "drop")],
        [json.dumps(body).encode()],
    )


@dataclass
class Proxy:
    port: int
    upstream: StubUpstream
    ledger: BudgetLedger
    logs: list[dict[str, object]]

    def token(self, task_id: str = "T-1", ttl: int = 600, budget: int = 10_000) -> str:
        return mint(SECRET, task_id, ttl, budget)

    def post(
        self,
        body: object,
        token: str | None = None,
        path: str = "/v1/messages",
        header: str = "authorization",
    ) -> tuple[int, dict[str, str], bytes]:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"content-type": "application/json", "anthropic-version": "2023-06-01"}
        if token is not None:
            headers[header] = f"Bearer {token}" if header == "authorization" else token
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        conn.request("POST", path, body=raw, headers=headers)
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp.status, {k.lower(): v for k, v in resp.getheaders()}, data


@pytest.fixture
def proxy() -> Iterator[Proxy]:
    upstream = StubUpstream(response=message_response())
    ledger = BudgetLedger()
    logs: list[dict[str, object]] = []
    config = Config(secret=SECRET, api_key=API_KEY, upstream=upstream, log=logs.append)
    server = make_server(config, "127.0.0.1", 0, ledger)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield Proxy(server.server_address[1], upstream, ledger, logs)
    finally:
        server.shutdown()
        server.server_close()


def request(
    model: str = "claude-sonnet-5-5", max_tokens: int = 100, **extra: object
) -> dict[str, object]:
    return {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": "PROMPT-CANARY-7f3a"}],
        **extra,
    }
