"""Which seed roles start visible and which wait in the hidden pool for scans to release.

Visible (at most `VISIBLE_ROLES`, deterministic):
1. every role with history beyond a status change: applied/unknown/rejected, or referenced by a
   chat, follow-up, rejection, recruiter, experience record, campaign activity or kit event;
2. then the newest Act now roles (new, not filtered);
3. then the newest other roles, until the total is reached.
The rest go to `role_pool`, released oldest-posted first by the scan.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

VISIBLE_ROLES = 300
_DEEP_STATUSES = frozenset({"applied", "unknown", "rejected", "interview", "offer"})
_REFERENCING = (
    "chats",
    "followups",
    "rejections",
    "role_recruiters",
    "experience",
    "campaign_activity",
)
_HISTORY_EVENTS = frozenset({"kit_written", "kit_regenerated", "recruiter", "followed_up"})

type Row = Mapping[str, Any]


def _newest(roles: Sequence[Row]) -> list[Row]:
    return sorted(roles, key=lambda r: (str(r.get("found_at") or ""), int(r["id"])), reverse=True)


def visible_role_ids(data: Mapping[str, Sequence[Row]], limit: int = VISIBLE_ROLES) -> set[int]:
    roles = list(data["roles"])
    referenced: set[object] = set()
    for name in _REFERENCING:
        referenced |= {row.get("role_id") for row in data.get(name, ())}
    referenced |= {
        e.get("role_id") for e in data.get("role_events", ()) if e.get("kind") in _HISTORY_EVENTS
    }
    deep = [r for r in roles if r["status"] in _DEEP_STATUSES or r["id"] in referenced]
    chosen: list[int] = [int(r["id"]) for r in _newest(deep)][:limit]
    taken = set(chosen)
    act_now = [r for r in roles if r["status"] == "new" and r.get("stage") != "filtered"]
    for group in (act_now, roles):
        for role in _newest(group):
            if len(chosen) >= limit:
                return set(chosen)
            if int(role["id"]) not in taken:
                chosen.append(int(role["id"]))
                taken.add(int(role["id"]))
    return set(chosen)
