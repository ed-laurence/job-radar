"""Credential-injecting forwarder for the Anthropic Messages API.

The sandbox sends ``Authorization: Bearer <per-task token>`` (or ``x-api-key: <token>``). The
proxy checks the token, the model allowlist, the model's price and the task's USD budget, swaps
in the real key from its own environment and forwards. It logs metadata only (task id, model,
token counts, costs, status), never prompts or completions, and redacts credentials from every
error it returns or logs.

Budget (micro-USD, from the jrt2 token): before forwarding, reserve the request body at the
input price (len/4 tokens; count_tokens is not available offline) plus
min(max_tokens, LLM_PROXY_RESERVE_OUTPUT_TOKENS) at the output price; afterwards settle the real
cost from the response usage (streamed or not). An upstream error with no usage costs nothing; a
success with no usage is charged the reservation. ``GET /v1/usage`` (task token) returns
``{"usd_micro_used", "usd_micro_budget", "requests", "budget_refusals"}``.
"""

from __future__ import annotations

import http.client
import json
import os
import sys
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import IO, Protocol
from urllib.parse import urlsplit

from .budget import BudgetExceededError, BudgetLedger, Reservation
from .jsonval import as_int, as_object
from .pricing import (
    Price,
    PriceTableError,
    TokenCounts,
    cost_usd_micro,
    load_price_table,
    reserve_usd_micro,
    usd_to_micro,
)
from .redact import redact, redact_bytes
from .tokens import Claims, TokenError, check_secret, verify

DEFAULT_MODELS = ("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5")
MESSAGES = "/v1/messages"
COUNT_TOKENS = "/v1/messages/count_tokens"
USAGE = "/v1/usage"
DEFAULT_PRICES_PATH = "/etc/llm-proxy/models.yaml"
DEFAULT_MAX_BUDGET_USD = "20.00"
DEFAULT_RESERVE_OUTPUT_TOKENS = 8192
ALLOWED_QUERIES = ("", "beta=true")
FORWARD_REQUEST_HEADERS = ("anthropic-version", "anthropic-beta", "content-type", "accept")
FORWARD_RESPONSE_HEADERS = ("content-type", "request-id", "retry-after")
FORWARD_RESPONSE_PREFIXES = ("anthropic-ratelimit-",)
MAX_SSE_LINE = 1 << 20


# ------------------------------------------------------------------ upstream port


class UpstreamResponse(Protocol):
    status: int
    headers: list[tuple[str, str]]

    def chunks(self) -> Iterator[bytes]: ...

    def close(self) -> None: ...


class Upstream(Protocol):
    def send(
        self, method: str, path: str, headers: dict[str, str], body: bytes
    ) -> UpstreamResponse: ...


@dataclass
class _HttpResponse:
    _resp: http.client.HTTPResponse
    _conn: http.client.HTTPConnection
    status: int = 0
    headers: list[tuple[str, str]] = field(default_factory=list[tuple[str, str]])

    def chunks(self) -> Iterator[bytes]:
        while True:
            data = self._resp.read1(65536)
            if not data:
                return
            yield data

    def close(self) -> None:
        self._conn.close()


@dataclass
class HttpUpstream:
    """Anthropic API over HTTPS (or plain HTTP for a local stub). Base URL is fixed config."""

    base_url: str
    timeout_s: float = 600.0

    def send(
        self, method: str, path: str, headers: dict[str, str], body: bytes
    ) -> UpstreamResponse:
        url = urlsplit(self.base_url)
        if url.scheme not in ("https", "http") or not url.hostname:
            raise ValueError("upstream base URL must be http(s)://host[:port]")
        conn_cls = (
            http.client.HTTPSConnection if url.scheme == "https" else http.client.HTTPConnection
        )
        conn = conn_cls(url.hostname, url.port, timeout=self.timeout_s)
        conn.request(method, url.path.rstrip("/") + path, body=body, headers=headers)
        resp = conn.getresponse()
        return _HttpResponse(resp, conn, resp.status, list(resp.getheaders()))


# ------------------------------------------------------------------ config


@dataclass(frozen=True)
class Config:
    secret: bytes
    api_key: str
    upstream: Upstream
    models: frozenset[str] = frozenset(DEFAULT_MODELS)
    # Empty = every model refused (fail closed); production loads ai/models.yaml.
    prices: Mapping[str, Price] = field(default_factory=dict[str, Price])
    max_usd_micro: int = 20_000_000
    reserve_output_tokens: int = DEFAULT_RESERVE_OUTPUT_TOKENS
    max_body: int = 32 << 20
    log: Callable[[dict[str, object]], None] = field(default=lambda _e: None)

    @property
    def secrets(self) -> tuple[str, ...]:
        return (self.api_key, self.secret.decode("utf-8", "replace"))


def json_logger(stream: IO[str]) -> Callable[[dict[str, object]], None]:
    def log(event: dict[str, object]) -> None:
        stream.write(json.dumps(event, sort_keys=True) + "\n")
        stream.flush()

    return log


def config_from_env(env: dict[str, str]) -> Config:
    backend = env.get("LLM_PROXY_BACKEND", "anthropic")
    if backend != "anthropic":
        raise SystemExit(f"LLM_PROXY_BACKEND={backend} is not supported yet (only anthropic)")
    secret = env.get("LLM_PROXY_SECRET", "").encode()
    try:
        check_secret(secret)
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    api_key = env.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise SystemExit("ANTHROPIC_API_KEY is not set in the llm-proxy environment")
    models = env.get("LLM_PROXY_MODELS")
    prices_path = Path(env.get("LLM_PROXY_PRICES") or DEFAULT_PRICES_PATH)
    try:
        prices = load_price_table(prices_path)
        max_usd_micro = usd_to_micro(env.get("LLM_PROXY_MAX_BUDGET_USD") or DEFAULT_MAX_BUDGET_USD)
        reserve_output = int(
            env.get("LLM_PROXY_RESERVE_OUTPUT_TOKENS") or DEFAULT_RESERVE_OUTPUT_TOKENS
        )
    except PriceTableError as exc:
        raise SystemExit(f"llm-proxy price table (LLM_PROXY_PRICES): {exc}") from None
    except ValueError as exc:
        raise SystemExit(f"llm-proxy budget settings: {exc}") from None
    if reserve_output <= 0:
        raise SystemExit("LLM_PROXY_RESERVE_OUTPUT_TOKENS must be positive")
    return Config(
        secret=secret,
        api_key=api_key,
        upstream=HttpUpstream(env.get("LLM_PROXY_UPSTREAM", "https://api.anthropic.com")),
        models=frozenset(m.strip() for m in models.split(",") if m.strip())
        if models
        else frozenset(DEFAULT_MODELS),
        prices=prices,
        max_usd_micro=max_usd_micro,
        reserve_output_tokens=reserve_output,
        log=json_logger(sys.stderr),
    )


# ------------------------------------------------------------------ usage accounting


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    seen: bool = False

    def update(self, raw: object) -> None:
        usage = as_object(raw)
        if usage is None:
            return
        for name in (
            "input_tokens",
            "output_tokens",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
        ):
            value = as_int(usage.get(name))
            if value is not None and value >= getattr(self, name):
                setattr(self, name, value)
                self.seen = True

    def counts(self) -> TokenCounts:
        return TokenCounts(
            self.input_tokens,
            self.output_tokens,
            self.cache_creation_input_tokens,
            self.cache_read_input_tokens,
        )


class SseUsageParser:
    """Reads ``message_start`` / ``message_delta`` usage from a passing SSE stream."""

    def __init__(self, usage: Usage) -> None:
        self.usage = usage
        self._buf = b""

    def feed(self, data: bytes) -> None:
        self._buf += data
        *lines, self._buf = self._buf.split(b"\n")
        if len(self._buf) > MAX_SSE_LINE:
            self._buf = b""
        for line in lines:
            if not line.startswith(b"data:"):
                continue
            try:
                event = as_object(json.loads(line[5:].strip()))
            except ValueError:
                continue
            if event is None:
                continue
            message = as_object(event.get("message"))
            if event.get("type") == "message_start" and message is not None:
                self.usage.update(message.get("usage"))
            elif event.get("type") == "message_delta":
                self.usage.update(event.get("usage"))


# ------------------------------------------------------------------ HTTP handler


class ProxyError(Exception):
    def __init__(self, status: int, kind: str, message: str) -> None:
        super().__init__(message)
        self.status, self.kind, self.message = status, kind, message


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "llm-proxy"
    sys_version = ""
    config: Config
    ledger: BudgetLedger

    def log_message(self, format: str, *args: object) -> None:
        """Silence the default access log (it would print request lines)."""

    # -- helpers
    def _send_json(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, err: ProxyError) -> None:
        message = redact(err.message, self.config.secrets)
        self._send_json(
            err.status, {"type": "error", "error": {"type": err.kind, "message": message}}
        )

    def _token(self) -> str | None:
        auth = self.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        return self.headers.get("x-api-key") or None

    def _body_length(self) -> int:
        if "chunked" in self.headers.get("transfer-encoding", "").lower():
            raise ProxyError(411, "invalid_request_error", "content-length required")
        try:
            length = int(self.headers.get("content-length", ""))
        except ValueError:
            raise ProxyError(411, "invalid_request_error", "content-length required") from None
        if length < 0 or length > self.config.max_body:
            raise ProxyError(413, "request_too_large", "request body too large")
        return length

    def _read_body(self) -> bytes:
        length = self._body_length()
        self._body_read = True
        return self.rfile.read(length)

    def _finish_unread_body(self) -> None:
        """Before an early error reply: drain a bounded body, else drop the connection."""
        if getattr(self, "_body_read", False):
            return
        try:
            self.rfile.read(self._body_length())
        except ProxyError:
            self.close_connection = True

    # -- routes
    def _claims(self, event: dict[str, object]) -> Claims:
        try:
            return verify(self.config.secret, self._token())
        except TokenError as err:
            event["auth"] = err.reason
            raise ProxyError(401, "authentication_error", "invalid task token") from None

    def do_GET(self) -> None:
        if self.path == "/healthz":
            self._send_json(200, {"status": "ok"})
        elif self.path == USAGE:
            self._usage()
        else:
            self._send_json(404, {"type": "error", "error": {"type": "not_found_error"}})

    def _usage(self) -> None:
        event: dict[str, object] = {"event": "usage_request"}
        try:
            claims = self._claims(event)
            usage = self.ledger.usage(claims.task_id)
            event.update(task_id=claims.task_id, status=200)
            self._send_json(
                200,
                {
                    "usd_micro_used": usage.used,
                    "usd_micro_budget": claims.usd_micro,
                    "requests": usage.requests,
                    "budget_refusals": usage.refusals,
                },
            )
        except ProxyError as err:
            event.update(status=err.status, error=err.kind)
            self._send_error(err)
        finally:
            self.config.log(event)

    def do_POST(self) -> None:
        started = time.monotonic()
        event: dict[str, object] = {"event": "llm_request"}
        self._body_read = False
        self._streaming = False
        try:
            self._handle_post(event)
        except ProxyError as err:
            event.update(status=err.status, error=err.kind)
            self._finish_unread_body()
            self._send_error(err)
        except (BrokenPipeError, ConnectionResetError):
            event.update(error="client_disconnected")
        except Exception as exc:
            # Last-resort guard: only the exception class is logged; the reply is generic.
            event.update(status=502, error=type(exc).__name__)
            if self._streaming:
                self.close_connection = True  # mid-stream: a JSON error would corrupt framing
            else:
                self._send_error(ProxyError(502, "api_error", "upstream request failed"))
        finally:
            event["duration_ms"] = int((time.monotonic() - started) * 1000)
            self.config.log(event)

    def _handle_post(self, event: dict[str, object]) -> None:
        url = urlsplit(self.path)
        if url.path not in (MESSAGES, COUNT_TOKENS) or url.query not in ALLOWED_QUERIES:
            raise ProxyError(404, "not_found_error", "unsupported endpoint")
        claims = self._claims(event)
        if claims.usd_micro > self.config.max_usd_micro:
            raise ProxyError(403, "permission_error", "token budget above proxy maximum")
        event["task_id"] = claims.task_id
        body = self._read_body()
        try:
            request = as_object(json.loads(body))
        except ValueError:
            raise ProxyError(400, "invalid_request_error", "body must be JSON") from None
        if request is None:
            raise ProxyError(400, "invalid_request_error", "body must be a JSON object")
        model = request.get("model")
        event["model"] = model if isinstance(model, str) and model in self.config.models else None
        if not isinstance(model, str) or model not in self.config.models:
            raise ProxyError(403, "permission_error", "model not allowed")
        price = self.config.prices.get(model)
        if price is None:  # fail closed: no price, no way to hold the budget
            raise ProxyError(403, "permission_error", "model has no price in the price table")

        reservation: Reservation | None = None
        if url.path == MESSAGES:
            max_tokens = as_int(request.get("max_tokens"))
            if max_tokens is None or max_tokens <= 0:
                raise ProxyError(400, "invalid_request_error", "max_tokens must be positive")
            amount = reserve_usd_micro(
                price, len(body), max_tokens, self.config.reserve_output_tokens
            )
            event.update(reserved_usd_micro=amount, budget_usd_micro=claims.usd_micro)
            try:
                reservation = self.ledger.reserve(claims.task_id, claims.usd_micro, amount)
            except BudgetExceededError:
                event["used_usd_micro"] = self.ledger.used(claims.task_id)
                raise ProxyError(403, "permission_error", "task token budget exhausted") from None
        target = url.path + (f"?{url.query}" if url.query else "")
        self._forward(target, body, price, reservation, event)

    def _forward(
        self,
        path: str,
        body: bytes,
        price: Price,
        reservation: Reservation | None,
        event: dict[str, object],
    ) -> None:
        headers = {k: v for k in FORWARD_REQUEST_HEADERS if (v := self.headers.get(k))}
        headers["x-api-key"] = self.config.api_key
        headers["content-length"] = str(len(body))
        usage = Usage()
        try:
            resp = self.config.upstream.send("POST", path, headers, body)
        except Exception:
            if reservation:
                self.ledger.settle(reservation, 0)
            raise
        try:
            self._relay(resp, usage)
        finally:
            resp.close()
            if reservation:
                if usage.seen:
                    charged = cost_usd_micro(price, usage.counts())
                elif resp.status >= 400:
                    charged = 0  # refused upstream before any work: nothing billed
                else:
                    charged = reservation.amount  # success without usage: fail closed
                event["cost_usd_micro"] = charged
                event["used_usd_micro"] = self.ledger.settle(reservation, charged)
            event.update(
                status=resp.status,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_creation_input_tokens=usage.cache_creation_input_tokens,
                cache_read_input_tokens=usage.cache_read_input_tokens,
            )

    def _relay(self, resp: UpstreamResponse, usage: Usage) -> None:
        kept = [
            (k, v)
            for k, v in resp.headers
            if k.lower() in FORWARD_RESPONSE_HEADERS
            or k.lower().startswith(FORWARD_RESPONSE_PREFIXES)
        ]
        ctype = next((v for k, v in kept if k.lower() == "content-type"), "")
        if resp.status < 400 and ctype.startswith("text/event-stream"):
            parser = SseUsageParser(usage)
            self.send_response(resp.status)
            for k, v in kept:
                self.send_header(k, v)
            self.send_header("transfer-encoding", "chunked")
            self.end_headers()
            self._streaming = True
            for chunk in resp.chunks():
                parser.feed(chunk)
                self.wfile.write(b"%x\r\n%s\r\n" % (len(chunk), chunk))
                self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n")
            return
        data = b"".join(resp.chunks())
        if resp.status >= 400:
            data = redact_bytes(data, self.config.secrets)
        else:
            try:
                parsed = as_object(json.loads(data))
                if parsed is not None:
                    usage.update(parsed.get("usage"))
            except ValueError:
                pass
        self.send_response(resp.status)
        for k, v in kept:
            self.send_header(k, v)
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def make_server(
    config: Config, host: str = "127.0.0.1", port: int = 8080, ledger: BudgetLedger | None = None
) -> ThreadingHTTPServer:
    handler = type(
        "BoundHandler", (Handler,), {"config": config, "ledger": ledger or BudgetLedger()}
    )
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    return server


def main() -> None:
    config = config_from_env(dict(os.environ))
    # The container sets LLM_PROXY_LISTEN=0.0.0.0:8080 (sandbox network only, no host port).
    host, _, port = os.environ.get("LLM_PROXY_LISTEN", "127.0.0.1:8080").rpartition(":")
    server = make_server(config, host or "127.0.0.1", int(port))
    config.log({"event": "listening", "port": int(port), "models": sorted(config.models)})
    server.serve_forever()
