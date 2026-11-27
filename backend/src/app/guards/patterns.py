"""Secret patterns for the redaction filter: a compact subset of the gitleaks rule set.

Patterns never use whitespace-sensitive anchors that a chunk boundary could break, so the
streaming redactor can run them over a sliding window.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class SecretPattern:
    name: str
    regex: re.Pattern[str]


def _rule(name: str, pattern: str, flags: int = 0) -> SecretPattern:
    return SecretPattern(name=name, regex=re.compile(pattern, flags))


SECRET_PATTERNS: tuple[SecretPattern, ...] = (
    _rule(
        "private-key",
        r"-----BEGIN[A-Z ]{0,30}PRIVATE KEY-----[\s\S]*?-----END[A-Z ]{0,30}PRIVATE KEY-----",
    ),
    _rule("aws-access-key", r"(?:AKIA|ASIA)[0-9A-Z]{16}"),
    _rule("gcp-api-key", r"AIza[0-9A-Za-z_\-]{35}"),
    _rule("google-oauth-token", r"ya29\.[0-9A-Za-z_\-]{20,}"),
    _rule(
        "gcp-service-account",
        r"[a-z][a-z0-9-]{4,28}[a-z0-9]@[a-z][a-z0-9-]{4,28}[a-z0-9]"
        r"\.iam\.gserviceaccount\.com",
    ),
    _rule("gcp-private-key-id", r"\"private_key_id\"\s*:\s*\"[0-9a-f]{40}\""),
    _rule("github-token", r"(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,255}"),
    _rule("github-fine-grained-pat", r"github_pat_[A-Za-z0-9_]{22,255}"),
    _rule("anthropic-key", r"sk-ant-[A-Za-z0-9_\-]{20,}"),
    _rule("generic-sk-key", r"sk-(?:proj-)?[A-Za-z0-9_\-]{32,}"),
    _rule("stripe-live-key", r"(?:sk|rk)_live_[0-9A-Za-z]{24,}"),
    _rule("slack-token", r"xox[abposr]-[A-Za-z0-9-]{10,}"),
    _rule("jwt", r"eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
    _rule("bearer-token", r"\bbearer\s+[A-Za-z0-9._~+/\-]{20,}=*", re.IGNORECASE),
    _rule(
        "assigned-secret",
        r"\b(?:password|passwd|secret|api[_-]?key|access[_-]?token)\s*[:=]\s*['\"]?[^\s'\"]{8,}",
        re.IGNORECASE,
    ),
)

# A key block that never closed. Only applied once the text is complete (non-streaming, or at
# the end of a stream), because mid-stream the END line may still be on its way.
OPEN_PRIVATE_KEY = re.compile(r"-----BEGIN[A-Z ]{0,30}PRIVATE KEY-----[\s\S]*\Z")
PRIVATE_KEY_START = re.compile(r"-----BEGIN[A-Z ]{0,30}PRIVATE KEY-----")
