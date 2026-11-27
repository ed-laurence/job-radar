"""llm-proxy HTTP behaviour against a stub upstream (no network beyond loopback)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import http.client
import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import ClassVar

import pytest
from conftest import (
    API_KEY,
    PRICES,
    SECRET,
    Proxy,
    StubResponse,
    StubUpstream,
    message_response,
    request,
)
from llm_proxy.budget import BudgetLedger
from llm_proxy.pricing import TokenCounts, cost_usd_micro, reserve_usd_micro
from llm_proxy.server import Config, HttpUpstream, config_from_env, make_server
from llm_proxy.tokens import mint

SONNET = PRICES["claude-sonnet-5-5"]


def _body_len(body: dict[str, object]) -> int:
    return len(json.dumps(body).encode())


def _reserve(body: dict[str, object]) -> int:
    max_tokens = body["max_tokens"]
    assert isinstance(max_tokens, int)
    return reserve_usd_micro(SONNET, _body_len(body), max_tokens, 8192)


def _sonnet_cost(inp: int, out: int, write: int = 0, read: int = 0) -> int:
    return cost_usd_micro(SONNET, TokenCounts(inp, out, write, read))


def _error_type(data: bytes) -> str:
    return json.loads(data)["error"]["type"]


# ------------------------------------------------------------------ refusals


def test_missing_token_is_refused(proxy: Proxy) -> None:
    status, _, data = proxy.post(request())
    assert status == 401
    assert _error_type(data) == "authentication_error"
    assert proxy.upstream.calls == []


def test_expired_token_is_refused(proxy: Proxy) -> None:
    status, _, _ = proxy.post(request(), proxy.token(ttl=-5))
    assert status == 401
    assert proxy.upstream.calls == []
    assert proxy.logs[-1]["auth"] == "expired"


def test_forged_token_is_refused(proxy: Proxy) -> None:
    status, _, _ = proxy.post(request(), mint(b"z" * 40, "T-1", 600, 1000))
    assert status == 401


def _jrt1_token(secret: bytes) -> str:
    """A correctly signed token in the old (token-budget) format."""
    payload = json.dumps({"b": 200_000, "exp": 4_000_000_000, "t": "T-1"}, separators=(",", ":"))
    p64 = base64.urlsafe_b64encode(payload.encode()).rstrip(b"=").decode()
    sig = hmac.new(secret, f"jrt1.{p64}".encode(), hashlib.sha256).digest()
    return f"jrt1.{p64}.{base64.urlsafe_b64encode(sig).rstrip(b'=').decode()}"


def test_jrt1_token_is_refused_with_401(proxy: Proxy) -> None:
    status, _, data = proxy.post(request(), _jrt1_token(SECRET))
    assert status == 401
    assert _error_type(data) == "authentication_error"
    assert proxy.logs[-1]["auth"] == "legacy_token"
    assert proxy.upstream.calls == []


def test_over_budget_token_is_refused(proxy: Proxy) -> None:
    token = proxy.token(budget=50)
    status, _, data = proxy.post(request(max_tokens=100), token)
    assert status == 403
    assert "budget" in json.loads(data)["error"]["message"]
    assert proxy.upstream.calls == []


def test_budget_is_consumed_by_real_cost(proxy: Proxy) -> None:
    body = request(max_tokens=20)
    cost = _sonnet_cost(10, 5)  # the stub's usage: 10 in, 5 out
    token = proxy.token(budget=cost + _reserve(body))
    assert proxy.post(body, token)[0] == 200
    assert proxy.ledger.used("T-1") == cost
    assert proxy.post(body, token)[0] == 200  # cost + reserve <= budget
    assert proxy.post(body, token)[0] == 403  # 2 * cost + reserve > budget
    assert proxy.ledger.used("T-1") == 2 * cost


def test_reservation_estimates_input_from_the_body_and_caps_output(proxy: Proxy) -> None:
    # max_tokens 32000 holds only 8192 output tokens; the body is priced at len/4 input tokens.
    body = request(max_tokens=32_000, system="x" * 4000)
    expected = reserve_usd_micro(SONNET, _body_len(body), 8192, 8192)
    assert proxy.post(body, proxy.token(budget=expected))[0] == 200
    assert proxy.logs[-1]["reserved_usd_micro"] == expected
    assert proxy.post(body, proxy.token("T-2", budget=expected - 1))[0] == 403


def test_upstream_error_without_usage_costs_nothing(proxy: Proxy) -> None:
    proxy.upstream.response = StubResponse(
        529, [("content-type", "application/json")], [b'{"type":"error"}']
    )
    assert proxy.post(request(), proxy.token())[0] == 529
    assert proxy.ledger.used("T-1") == 0


def test_success_without_usage_is_charged_the_reservation(proxy: Proxy) -> None:
    proxy.upstream.response = StubResponse(200, [("content-type", "application/json")], [b"{}"])
    body = request()
    assert proxy.post(body, proxy.token())[0] == 200
    assert proxy.ledger.used("T-1") == _reserve(body)


def test_unpriced_model_is_refused_even_when_allowed() -> None:
    upstream = StubUpstream(response=message_response())
    config = Config(
        secret=SECRET,
        api_key=API_KEY,
        upstream=upstream,
        models=frozenset({"claude-unpriced-9"}),
        prices=PRICES,
    )
    server = make_server(config, "127.0.0.1", 0, BudgetLedger())
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        p = Proxy(server.server_address[1], upstream, BudgetLedger(), [])
        status, _, data = p.post(request(model="claude-unpriced-9"), p.token())
    finally:
        server.shutdown()
        server.server_close()
    assert status == 403
    assert "price" in json.loads(data)["error"]["message"]
    assert upstream.calls == []


def test_usage_endpoint_reports_spend_for_the_token_task(proxy: Proxy) -> None:
    token = proxy.token(budget=2_000_000)
    assert proxy.post(request(), token)[0] == 200
    assert proxy.post(request(), token)[0] == 200
    proxy.post(request(), proxy.token("T-other"))
    status, data = proxy.get("/v1/usage", token)
    assert status == 200
    assert json.loads(data) == {
        "usd_micro_used": 2 * _sonnet_cost(10, 5),
        "usd_micro_budget": 2_000_000,
        "requests": 2,
        "budget_refusals": 0,
    }


def test_usage_endpoint_counts_budget_refusals(proxy: Proxy) -> None:
    token = proxy.token(budget=10)
    assert proxy.post(request(), token)[0] == 403
    status, data = proxy.get("/v1/usage", token)
    assert status == 200
    assert json.loads(data)["budget_refusals"] == 1


def test_usage_endpoint_needs_a_valid_token(proxy: Proxy) -> None:
    assert proxy.get("/v1/usage")[0] == 401
    assert proxy.get("/v1/usage", _jrt1_token(SECRET))[0] == 401
    assert proxy.get("/v1/usage", mint(b"z" * 40, "T-1", 600, 1000))[0] == 401


def test_config_requires_a_readable_price_table(tmp_path: Path) -> None:
    env = {"LLM_PROXY_SECRET": SECRET.decode(), "ANTHROPIC_API_KEY": API_KEY}
    with pytest.raises(SystemExit) as err:
        config_from_env({**env, "LLM_PROXY_PRICES": str(tmp_path / "absent.yaml")})
    assert "price table" in str(err.value)
    bad = tmp_path / "bad.yaml"
    bad.write_text("pricing:\n  m: {input: 1}\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        config_from_env({**env, "LLM_PROXY_PRICES": str(bad)})
    good = tmp_path / "models.yaml"
    good.write_text("pricing:\n  claude-opus-5-5: {input: 4, output: 20}\n", encoding="utf-8")
    config = config_from_env({**env, "LLM_PROXY_PRICES": str(good)})
    assert set(config.prices) == {"claude-opus-5-5"}
    assert config.max_usd_micro == 20_000_000 and config.reserve_output_tokens == 8192


def test_model_outside_allowlist_is_refused(proxy: Proxy) -> None:
    status, _, data = proxy.post(request(model="claude-3-opus"), proxy.token())
    assert status == 403
    assert _error_type(data) == "permission_error"
    assert proxy.upstream.calls == []


def test_unknown_path_and_query_are_refused(proxy: Proxy) -> None:
    assert proxy.post(request(), proxy.token(), path="/v1/files")[0] == 404
    assert proxy.post(request(), proxy.token(), path="/v1/messages?x=1")[0] == 404


def test_bad_bodies_are_refused(proxy: Proxy) -> None:
    assert proxy.post(b"not json", proxy.token())[0] == 400
    assert proxy.post(request(max_tokens=0), proxy.token())[0] == 400


def test_oversized_body_is_refused_before_reading_it(proxy: Proxy) -> None:
    conn = http.client.HTTPConnection("127.0.0.1", proxy.port, timeout=5)
    conn.putrequest("POST", "/v1/messages")
    conn.putheader("authorization", f"Bearer {proxy.token()}")
    conn.putheader("content-length", str(64 << 20))
    conn.endheaders()  # no body sent: the proxy must answer from the header alone
    resp = conn.getresponse()
    assert resp.status == 413
    conn.close()
    assert proxy.upstream.calls == []


def test_chunked_upload_is_refused(proxy: Proxy) -> None:
    conn = http.client.HTTPConnection("127.0.0.1", proxy.port, timeout=5)
    conn.putrequest("POST", "/v1/messages")
    conn.putheader("authorization", f"Bearer {proxy.token()}")
    conn.putheader("transfer-encoding", "chunked")
    conn.endheaders()
    resp = conn.getresponse()
    assert resp.status == 411
    conn.close()


# ------------------------------------------------------------------ forwarding


def test_forwards_with_real_key_and_without_client_token(proxy: Proxy) -> None:
    token = proxy.token()
    status, headers, data = proxy.post(request(), token, path="/v1/messages?beta=true")
    assert status == 200
    assert json.loads(data)["content"][0]["text"] == "hi"
    assert headers["request-id"] == "req_1"
    assert "x-internal" not in headers
    method, path, sent, body = proxy.upstream.calls[0]
    assert (method, path) == ("POST", "/v1/messages?beta=true")
    assert sent["x-api-key"] == API_KEY
    assert "authorization" not in {k.lower() for k in sent}
    assert token not in json.dumps(sent)
    assert sent["anthropic-version"] == "2023-06-01"
    assert json.loads(body)["model"] == "claude-sonnet-5-5"


def test_x_api_key_header_is_accepted_as_the_task_token(proxy: Proxy) -> None:
    assert proxy.post(request(), proxy.token(), header="x-api-key")[0] == 200


def test_count_tokens_is_not_charged(proxy: Proxy) -> None:
    proxy.upstream.response = StubResponse(
        200, [("content-type", "application/json")], [b'{"input_tokens": 99}']
    )
    body: dict[str, object] = {"model": "claude-haiku-5-5", "messages": []}
    assert proxy.post(body, proxy.token(), path="/v1/messages/count_tokens")[0] == 200
    assert proxy.ledger.used("T-1") == 0


def test_streaming_is_relayed_and_usage_counted(proxy: Proxy) -> None:
    events = [
        b'event: message_start\ndata: {"type":"message_start","message":'
        b'{"usage":{"input_tokens":30,"output_tokens":1}}}\n\n',
        b'event: content_block_delta\ndata: {"type":"content_block_delta"}\n\n',
        b'event: message_delta\ndata: {"type":"message_delta","usage":{"output_tokens":12}}\n\n',
    ]
    # Split one event across chunks to exercise the line buffer.
    chunks = [events[0][:20], events[0][20:], events[1], events[2]]
    proxy.upstream.response = StubResponse(200, [("content-type", "text/event-stream")], chunks)
    status, headers, data = proxy.post(request(stream=True), proxy.token())
    assert status == 200
    assert headers["content-type"] == "text/event-stream"
    assert data == b"".join(events)
    assert proxy.ledger.used("T-1") == _sonnet_cost(30, 12)


def test_streaming_cache_usage_is_priced_from_the_table(proxy: Proxy) -> None:
    events = [
        b'data: {"type":"message_start","message":{"usage":{"input_tokens":3,'
        b'"cache_creation_input_tokens":500,"cache_read_input_tokens":20000,'
        b'"output_tokens":1}}}\n\n',
        b'data: {"type":"message_delta","usage":{"output_tokens":400}}\n\n',
    ]
    proxy.upstream.response = StubResponse(200, [("content-type", "text/event-stream")], events)
    assert proxy.post(request(stream=True), proxy.token())[0] == 200
    assert proxy.ledger.used("T-1") == _sonnet_cost(3, 400, 500, 20_000)
    assert proxy.logs[-1]["cost_usd_micro"] == _sonnet_cost(3, 400, 500, 20_000)


class _BrokenStream(StubResponse):
    def chunks(self) -> Iterator[bytes]:
        yield b'data: {"type":"message_start","message":{"usage":{"input_tokens":7}}}\n\n'
        raise ConnectionError(f"upstream dropped; key {API_KEY}")


def test_mid_stream_failure_closes_without_a_json_error(proxy: Proxy) -> None:
    proxy.upstream.response = _BrokenStream(200, [("content-type", "text/event-stream")], [])
    conn = http.client.HTTPConnection("127.0.0.1", proxy.port, timeout=5)
    body = json.dumps(request(stream=True)).encode()
    conn.request(
        "POST", "/v1/messages", body=body, headers={"authorization": f"Bearer {proxy.token()}"}
    )
    resp = conn.getresponse()
    assert resp.status == 200
    try:
        data = resp.read()
    except http.client.IncompleteRead as err:
        data = err.partial
    conn.close()
    assert b"api_error" not in data
    assert API_KEY.encode() not in data
    assert proxy.ledger.used("T-1") == _sonnet_cost(7, 0)  # usage seen before the failure


def test_upstream_error_body_is_redacted(proxy: Proxy) -> None:
    leaked = json.dumps({"type": "error", "error": {"message": f"bad key {API_KEY}"}}).encode()
    proxy.upstream.response = StubResponse(401, [("content-type", "application/json")], [leaked])
    status, _, data = proxy.post(request(), proxy.token())
    assert status == 401
    assert API_KEY.encode() not in data


def test_upstream_exception_is_generic_and_releases_reservation(proxy: Proxy) -> None:
    proxy.upstream.error = ConnectionError(f"failed with key {API_KEY} and {SECRET.decode()}")
    status, _, data = proxy.post(request(), proxy.token())
    assert status == 502
    assert API_KEY.encode() not in data
    assert SECRET not in data
    assert proxy.ledger.used("T-1") == 0
    assert API_KEY not in json.dumps(proxy.logs)


def test_logs_are_metadata_only(proxy: Proxy) -> None:
    token = proxy.token()
    proxy.post(request(), token)
    entry = proxy.logs[-1]
    assert entry["task_id"] == "T-1"
    assert entry["model"] == "claude-sonnet-5-5"
    assert (entry["input_tokens"], entry["output_tokens"], entry["status"]) == (10, 5, 200)
    assert entry["cost_usd_micro"] == entry["used_usd_micro"] == _sonnet_cost(10, 5)
    assert entry["budget_usd_micro"] == 1_000_000
    assert isinstance(entry["reserved_usd_micro"], int)
    dumped = json.dumps(proxy.logs)
    assert "PROMPT-CANARY-7f3a" not in dumped
    assert token not in dumped
    assert API_KEY not in dumped


def test_healthz(proxy: Proxy) -> None:
    conn = http.client.HTTPConnection("127.0.0.1", proxy.port, timeout=5)
    conn.request("GET", "/healthz")
    resp = conn.getresponse()
    assert (resp.status, json.loads(resp.read())) == (200, {"status": "ok"})
    conn.close()


# ------------------------------------------------------------------ real HTTP upstream (loopback)


class _StubHandler(BaseHTTPRequestHandler):
    seen: ClassVar[dict[str, object]] = {}

    def do_POST(self) -> None:
        self.seen["path"] = self.path
        self.seen["key"] = self.headers.get("x-api-key")
        self.seen["body"] = self.rfile.read(int(self.headers["content-length"]))
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", "2")
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, format: str, *args: object) -> None:
        """Quiet."""


def test_http_upstream_sends_path_headers_and_body() -> None:
    server = HTTPServer(("127.0.0.1", 0), _StubHandler)
    threading.Thread(target=server.handle_request, daemon=True).start()
    upstream = HttpUpstream(f"http://127.0.0.1:{server.server_address[1]}")
    resp = upstream.send("POST", "/v1/messages", {"x-api-key": "k", "content-length": "2"}, b"{}")
    try:
        assert resp.status == 200
        assert b"".join(resp.chunks()) == b"{}"
    finally:
        resp.close()
        server.server_close()
    assert _StubHandler.seen == {"path": "/v1/messages", "key": "k", "body": b"{}"}
