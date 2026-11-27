"""The task's model spend from llm-proxy ``GET /v1/usage`` (stdlib only).

Same bearer as the model calls (``ANTHROPIC_AUTH_TOKEN``, a jrt2 task token). The response is
``{"usd_micro_used", "usd_micro_budget", "requests", "budget_refusals"}``. Every failure returns
``None``: the spend is reported when known, never invented.
"""

from __future__ import annotations

import http.client
import json
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from urllib.parse import urlsplit

from coordinator_client import as_object

USAGE_PATH = "/v1/usage"
MAX_BYTES = 4096

# (url, headers, timeout) -> (status, body)
Getter = Callable[[str, dict[str, str], float], tuple[int, bytes]]


def http_get(url: str, headers: dict[str, str], timeout: float) -> tuple[int, bytes]:
    parts = urlsplit(url)
    conn_cls = (
        http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
    )
    conn = conn_cls(parts.hostname or "", parts.port, timeout=timeout)
    try:
        conn.request("GET", parts.path or "/", headers=headers)
        resp = conn.getresponse()
        return resp.status, resp.read(MAX_BYTES + 1)
    finally:
        conn.close()


@dataclass(frozen=True)
class Spend:
    used: int
    budget: int
    requests: int
    refusals: int

    @property
    def exhausted(self) -> bool:
        return self.refusals > 0 or self.used >= self.budget

    def as_dict(self) -> dict[str, int]:
        return {
            "usd_micro_used": self.used,
            "usd_micro_budget": self.budget,
            "requests": self.requests,
            "budget_refusals": self.refusals,
        }


def usd(usd_micro: int) -> str:
    return f"${Decimal(usd_micro) / 1_000_000:.2f}"


def _int(data: dict[str, object], key: str) -> int | None:
    value = data.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


class UsageClient:
    def __init__(
        self, base_url: str, token: str, timeout: float = 5.0, getter: Getter = http_get
    ) -> None:
        self._url = base_url.rstrip("/") + USAGE_PATH
        self._token, self._timeout, self._get = token, timeout, getter

    def fetch(self) -> Spend | None:
        if not self._token or not self._url.startswith(("http://", "https://")):
            return None
        try:
            status, raw = self._get(
                self._url, {"authorization": f"Bearer {self._token}"}, self._timeout
            )
            if status != 200 or len(raw) > MAX_BYTES:
                return None
            data = as_object(json.loads(raw))
        except (OSError, ValueError, http.client.HTTPException):
            return None
        if data is None:
            return None
        fields = [
            _int(data, k)
            for k in ("usd_micro_used", "usd_micro_budget", "requests", "budget_refusals")
        ]
        used, budget, requests, refusals = fields
        if used is None or budget is None or requests is None:
            return None
        return Spend(used, budget, requests, refusals or 0)
