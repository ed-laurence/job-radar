"""Remove credentials from anything that leaves the proxy (errors, logs, upstream bodies)."""

from __future__ import annotations

import re
from collections.abc import Iterable

MARK = "[REDACTED]"
# Anthropic-style keys and bearer values, in case an upstream error echoes one we don't know.
_PATTERNS = (
    re.compile(r"sk-ant-[A-Za-z0-9_-]{8,}"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{8,}"),
)


def redact(text: str, secrets: Iterable[str]) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, MARK)
    text = _PATTERNS[0].sub(MARK, text)
    return _PATTERNS[1].sub(lambda m: m.group(1) + MARK, text)


def redact_bytes(data: bytes, secrets: Iterable[str]) -> bytes:
    return redact(data.decode("utf-8", "replace"), secrets).encode("utf-8")
