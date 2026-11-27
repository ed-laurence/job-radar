"""Run logs the host keeps: ``agent.log`` and ``steps.jsonl`` in the log dir (``/out``).

- ``agent.log``: one JSON line per model turn (turn number, tool names, token counts, the
  task's spend so far from llm-proxy) plus one ``stop`` line. Never prompts, tool inputs,
  tool output or file content.
- ``steps.jsonl``: a copy of every step posted to the coordinator (the same short text the
  board shows).

Writes are best effort: a full or read-only disk never stops the run.
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping, Sequence
from pathlib import Path

USAGE_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)
MAX_TOOL_NAMES = 20


def token_counts(usage: Mapping[str, object] | None) -> dict[str, int]:
    out: dict[str, int] = {}
    for name in USAGE_FIELDS:
        value = (usage or {}).get(name)
        out[name] = value if isinstance(value, int) and not isinstance(value, bool) else 0
    return out


class RunLog:
    def __init__(self, log_dir: Path) -> None:
        self.agent_log = log_dir / "agent.log"
        self.steps_log = log_dir / "steps.jsonl"
        self.turns = 0

    @staticmethod
    def _append(path: Path, entry: Mapping[str, object]) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, sort_keys=True, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def turn(
        self,
        tool_names: Sequence[str],
        usage: Mapping[str, object] | None,
        spend_usd_micro: int | None,
        budget_usd_micro: int | None,
    ) -> int:
        self.turns += 1
        self._append(
            self.agent_log,
            {
                "event": "turn",
                "turn": self.turns,
                "ts": int(time.time()),
                "tools": [n[:80] for n in tool_names[:MAX_TOOL_NAMES]],
                "tokens": token_counts(usage),
                "usd_micro_used": spend_usd_micro,
                "usd_micro_budget": budget_usd_micro,
            },
        )
        return self.turns

    def event(self, name: str, **fields: object) -> None:
        self._append(self.agent_log, {"event": name, "ts": int(time.time()), **fields})

    def step(self, kind: str, text: str) -> None:
        self._append(self.steps_log, {"kind": kind, "text": text, "ts": int(time.time())})
