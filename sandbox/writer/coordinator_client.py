"""Thin client for the coordinator's internal HTTP API (stdlib only; ADR-0009, no MCP).

Base URL ``COORDINATOR_URL`` (e.g. ``http://coordinator:8100``), bearer ``COORDINATOR_TOKEN``,
JSON bodies, RFC 9457 problem+json errors, 413 for oversized messages:

    GET  /v1/task?cursor=N          (paged; ``next_cursor`` until None)
    POST /v1/leases/claim           {paths, wait_s <= 60}
    POST /v1/leases/renew           {}
    POST /v1/leases/release         {paths?}
    POST /v1/steps                  {kind, text <= 2000}
    POST /v1/handoff                {handoff}
    POST /v1/help                   {text}

Connections go direct with http.client (it never consults *_PROXY): the coordinator is on the
internal sandbox network. Error messages never include the token.
"""

from __future__ import annotations

import http.client
import json
from collections.abc import Callable, Mapping
from typing import cast
from urllib.parse import urlencode, urlsplit

MAX_RESPONSE_BYTES = 256 * 1024
MAX_TASK_PAGES = 50
MAX_WAIT_S = 60
MAX_STEP_CHARS = 2000
STEP_KINDS = ("thought", "tool_call", "tool_result", "message")


class CoordinatorError(Exception):
    """Transport, HTTP or protocol error, with the RFC 9457 title/detail when there is one."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


# (method, url, headers, body | None, timeout) -> (status, lower-cased headers, body)
Transport = Callable[
    [str, str, dict[str, str], bytes | None, float], tuple[int, dict[str, str], bytes]
]


def http_transport(
    method: str, url: str, headers: dict[str, str], body: bytes | None, timeout: float
) -> tuple[int, dict[str, str], bytes]:
    parts = urlsplit(url)
    host, port = parts.hostname or "", parts.port
    conn = (
        http.client.HTTPSConnection(host, port, timeout=timeout)
        if parts.scheme == "https"
        else http.client.HTTPConnection(host, port, timeout=timeout)
    )
    target = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    try:
        conn.request(method, target, body=body, headers=headers)
        resp = conn.getresponse()
        data = resp.read(MAX_RESPONSE_BYTES + 1)
        return resp.status, {k.lower(): v for k, v in resp.getheaders()}, data
    except OSError as exc:
        raise CoordinatorError(f"coordinator unreachable: {type(exc).__name__}") from None
    finally:
        conn.close()


def as_object(value: object) -> dict[str, object] | None:
    return cast("dict[str, object]", value) if isinstance(value, dict) else None


def as_list(value: object) -> list[object]:
    return cast("list[object]", value) if isinstance(value, list) else []


def _is_list(value: object) -> bool:
    return isinstance(value, list)


def _problem(status: int, body: bytes) -> CoordinatorError:
    """RFC 9457: keep ``title``/``detail`` (server-written, no secrets), capped."""
    try:
        problem = as_object(json.loads(body)) or {}
    except (json.JSONDecodeError, UnicodeDecodeError):
        problem = {}
    parts = [str(problem[k])[:200] for k in ("title", "detail") if isinstance(problem.get(k), str)]
    if status == 413:
        parts.insert(0, "message too large: refused (shorten it; nothing was cut)")
    text = "; ".join(parts) or "request failed"
    return CoordinatorError(f"coordinator HTTP {status}: {text}", status)


class CoordinatorClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        timeout: float = 30.0,
        transport: Transport = http_transport,
    ) -> None:
        parts = urlsplit(base_url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise CoordinatorError("COORDINATOR_URL must be an http(s) base URL")
        if not token:
            raise CoordinatorError("COORDINATOR_TOKEN is missing")
        self._base = base_url.rstrip("/")
        self._token, self._timeout, self._send = token, timeout, transport

    def request(
        self,
        method: str,
        path: str,
        body: Mapping[str, object] | None = None,
        timeout: float | None = None,
    ) -> dict[str, object]:
        headers = {"authorization": f"Bearer {self._token}", "accept": "application/json"}
        data = None
        if body is not None:
            headers["content-type"] = "application/json"
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        status, _headers, raw = self._send(
            method, self._base + path, headers, data, timeout or self._timeout
        )
        if len(raw) > MAX_RESPONSE_BYTES:
            raise CoordinatorError("coordinator response too large")
        if status >= 400:
            raise _problem(status, raw)
        if not raw:
            return {}
        try:
            return as_object(json.loads(raw)) or {"result": json.loads(raw)}
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise CoordinatorError("coordinator returned non-JSON") from None

    # ------------------------------------------------------------ operations
    def task_page(self, cursor: int = 0) -> dict[str, object]:
        query = f"?{urlencode({'cursor': cursor})}" if cursor else ""
        return self.request("GET", f"/v1/task{query}")

    def get_task(self) -> dict[str, object]:
        """All pages, merged: ``brief`` concatenated, lists extended, the rest from page 1."""
        merged = self.task_page(0)
        cursor = merged.get("next_cursor")
        pages = 1
        while isinstance(cursor, int) and not isinstance(cursor, bool):
            if pages >= MAX_TASK_PAGES:
                raise CoordinatorError("get_task returned too many pages")
            page = self.task_page(cursor)
            pages += 1
            for key, value in page.items():
                current = merged.get(key)
                if key == "brief" and isinstance(current, str) and isinstance(value, str):
                    merged[key] = current + value
                elif _is_list(current) and _is_list(value):
                    merged[key] = [*as_list(current), *as_list(value)]
                elif key not in merged:
                    merged[key] = value
            cursor = page.get("next_cursor")
        for key in ("next_cursor", "total_items"):
            merged.pop(key, None)
        merged["brief_complete"] = True
        return merged

    def claim(self, paths: list[str], wait_s: int = 0) -> dict[str, object]:
        wait = max(0, min(MAX_WAIT_S, wait_s))
        return self.request(
            "POST",
            "/v1/leases/claim",
            {"paths": paths, "wait_s": wait},
            timeout=self._timeout + wait,
        )

    def renew(self) -> dict[str, object]:
        return self.request("POST", "/v1/leases/renew", {})

    def release(self, paths: list[str] | None = None) -> dict[str, object]:
        return self.request("POST", "/v1/leases/release", {"paths": paths} if paths else {})

    def step(self, kind: str, text: str, timeout: float | None = None) -> dict[str, object]:
        return self.request("POST", "/v1/steps", {"kind": kind, "text": text}, timeout=timeout)

    def handoff(
        self, handoff: Mapping[str, object], timeout: float | None = None
    ) -> dict[str, object]:
        return self.request("POST", "/v1/handoff", {"handoff": dict(handoff)}, timeout=timeout)

    def help(self, text: str) -> dict[str, object]:
        return self.request("POST", "/v1/help", {"text": text})


def granted_paths(claim_result: Mapping[str, object]) -> list[str]:
    """Paths from a claim response's ``granted`` list (``[{path, kind, expires_at}]``)."""
    out: list[str] = []
    for item in as_list(claim_result.get("granted")):
        obj = as_object(item)
        path = obj.get("path") if obj else item
        if isinstance(path, str) and path.strip():
            out.append(path)
    return out
