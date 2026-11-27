"""Job-search seed rows for one account (roles, events, rejections, CRM, LLM calls)."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, cast

from sqlalchemy import Table

from app.adapters.db import tables_jobs as jobs
from app.adapters.db import tables_platform as platform
from app.adapters.db import tables_scan as scan_tables
from app.domain.jobs.campaign import SEED_ANCHOR_NOTE
from app.domain.jobs.crm import company_key
from app.domain.jobs.ranking import spread
from app.jobs.seed_context import (
    EVENT_KINDS,
    LLM_FEATURES,
    REJECTION_CATEGORIES,
    WORK_MODES,
    Row,
    SeedContext,
    table_of,
)
from app.jobs.seed_pool import visible_role_ids

_CATEGORY_VOCAB = frozenset(
    [
        "skills_gap",
        "experience_level",
        "seniority",
        "location",
        "pay",
        "visa",
        "internal_candidate",
        "role_filled",
        "stronger_candidate",
        "culture_fit",
        "no_reason_given",
        "other",
    ]
)
_EVENT_VOCAB = frozenset(
    [
        "status",
        "note",
        "kit_regenerated",
        "kit_edit",
        "followed_up",
        "scored",
        "rank",
        "notified",
        "rating",
    ]
)


def _role_rows(ctx: SeedContext, visible: set[int]) -> list[Row]:
    by_status: dict[str, list[Row]] = defaultdict(list)
    for role in ctx.data["roles"]:
        if int(role["id"]) not in visible:
            continue
        by_status[str(role["status"])].append(role)
    rows: list[Row] = []
    for status, roles in by_status.items():
        ordered = sorted(roles, key=lambda r: (str(r["found_at"]), int(r["id"])), reverse=True)
        for rank, role in zip(spread(len(ordered)), ordered, strict=True):
            rows.append(_role_row(ctx, role, status, rank))
    return rows


def _pool_rows(ctx: SeedContext, visible: set[int]) -> list[Row]:
    """Held-back roles: the ad as a scraper would return it, released later by the scan."""
    rows: list[Row] = []
    for role in ctx.data["roles"]:
        if int(role["id"]) in visible:
            continue
        band = cast("dict[str, Any]", role.get("salary_band") or {})
        payload: Row = {
            "title": role["title"],
            "company": role.get("company"),
            "location": role.get("location"),
            "work_mode": WORK_MODES.get(str(role.get("work_mode") or "").lower()),
            "pay": band.get("display"),
            "url": role.get("url"),
            "description": role.get("description"),
            "keywords": role.get("keywords") or [],
        }
        rows.append(
            {
                "id": ctx.key("role_pool", role["id"]),
                "user_id": ctx.account.user_id,
                "source": "seed",
                "external_id": role["external_id"],
                "posted_at": ctx.at(role.get("posted_at")) or ctx.at(role.get("found_at")),
                "payload": payload,
            }
        )
    return rows


def _role_row(ctx: SeedContext, role: Row, status: str, rank: str) -> Row:
    band = cast("dict[str, Any]", role.get("salary_band") or {})
    mode = str(role.get("work_mode") or "").lower()
    found_at = ctx.at(role["found_at"])
    return {
        "id": ctx.key("roles", role["id"]),
        "user_id": ctx.account.user_id,
        "source": "seed",
        "external_id": role["external_id"],
        "url": role.get("url"),
        "title": role["title"],
        "company": role.get("company"),
        "location": role.get("location"),
        "work_mode": WORK_MODES.get(mode),
        "pay": band.get("display"),
        "posted_at": ctx.at(role.get("posted_at")),
        "found_at": found_at,
        "description": role.get("description"),
        "stage": role.get("stage"),
        "filter_reason": role.get("filter_reason"),
        "track": str(role["track"]).lower() if role.get("track") else None,
        "mission": role.get("mission"),
        "score": role.get("score"),
        "score_breakdown": role.get("score_breakdown"),
        "why": role.get("why"),
        "status": status,
        "rank": rank,
        "applied_at": ctx.at(role.get("applied_at")),
        "followed_up_at": ctx.at(role.get("followed_up_at")),
        "raw": {"keywords": role.get("keywords") or [], "seed_source": role.get("source")},
        "created_at": found_at,
        "updated_at": ctx.at(role.get("updated_at")) or found_at,
    }


def _category(raw: object) -> str:
    name = REJECTION_CATEGORIES.get(str(raw), str(raw))
    return name if name in _CATEGORY_VOCAB else "other"


def job_rows(ctx: SeedContext) -> list[tuple[Table, list[Row]]]:
    user, data = ctx.account.user_id, ctx.data
    visible = visible_role_ids(data)

    def role_ref(seed_id: object) -> object:
        # LLM calls about a pooled role keep their spend but lose the link.
        return ctx.ref("roles", seed_id) if seed_id in visible else None

    events = [
        {
            "id": ctx.key("role_events", e["id"]),
            "user_id": user,
            "role_id": ctx.key("roles", e["role_id"]),
            "at": ctx.at(e["at"]),
            "kind": _event_kind(e["kind"]),
            "from_value": e.get("from_value"),
            "to_value": e.get("to_value"),
        }
        for e in data["role_events"]
        if e["role_id"] in visible
    ]
    rejections = []
    for r in data["rejections"]:
        given = cast("list[dict[str, Any]]", r.get("reasons") or [])
        reasons = [{**reason, "category": _category(reason.get("category"))} for reason in given]
        rejections.append(
            {
                "id": ctx.key("rejections", r["id"]),
                "user_id": user,
                "role_id": ctx.key("roles", r["role_id"]),
                "stage": r.get("stage"),
                "rejected_at": ctx.at(r.get("rejected_at")),
                "rejection_text": r.get("rejection_text"),
                "from_name": r.get("from_name"),
                "channel": r.get("channel"),
                "request_message": r.get("request_message"),
                "request_status": r.get("request_status"),
                "feedback_text": r.get("feedback_text"),
                "reason_categories": sorted({reason["category"] for reason in reasons}),
                "reasons": reasons,
                "summary": r.get("summary"),
                "lesson": r.get("lesson"),
                "use_in_kits": bool(r.get("use_in_kits")),
                "updated_at": ctx.at(r.get("updated_at")),
            }
        )
    return [
        (table_of(jobs.Role), _role_rows(ctx, visible)),
        (table_of(scan_tables.RolePool), _pool_rows(ctx, visible)),
        (table_of(jobs.RoleEvent), events),
        (table_of(jobs.Rejection), rejections),
        (
            table_of(jobs.RoleChat),
            [
                {
                    "id": ctx.key("chats", c["id"]),
                    "user_id": user,
                    "role_id": ctx.key("roles", c["role_id"]),
                    "author": c["author"],
                    "content": c["content"],
                    "created_at": ctx.at(c["created_at"]),
                }
                for c in data["chats"]
            ],
        ),
        (table_of(jobs.Contact), [_contact_row(ctx, c) for c in data["contacts"]]),
        (
            table_of(jobs.Followup),
            [
                {
                    "id": ctx.key("followups", f["id"]),
                    "user_id": user,
                    "role_id": ctx.key("roles", f["role_id"]),
                    "priority": int(f.get("rank") or 0),
                    "target_name": f.get("target_name"),
                    "target_role": f.get("target_role"),
                    "source": f.get("source"),
                    "channel": f.get("channel"),
                    "email": f.get("email"),
                    "url": f.get("url"),
                    "why": f.get("why"),
                    "message": f.get("message"),
                    "status": f.get("status") or "suggested",
                    "sent_at": ctx.at(f.get("sent_at")),
                    "created_at": ctx.at(f.get("created_at")),
                }
                for f in data["followups"]
            ],
        ),
        (
            table_of(jobs.RoleRecruiter),
            [
                {
                    "id": ctx.key("role_recruiters", r["id"]),
                    "user_id": user,
                    "role_id": ctx.key("roles", r["role_id"]),
                    "name": r["name"],
                    "agency": r.get("agency"),
                    "url": r.get("url"),
                    "email": r.get("email"),
                    "created_at": ctx.at(r.get("created_at")),
                }
                for r in data["role_recruiters"]
            ],
        ),
        (table_of(jobs.Target), [_target_row(ctx, t) for t in data["targets"]]),
        (
            table_of(jobs.CampaignActivity),
            [
                {
                    "id": ctx.key("campaign_activity", a["id"]),
                    "user_id": user,
                    "on_date": ctx.day(a["on_date"]),
                    "kind": a["kind"],
                    "who": a.get("who"),
                    "company": a.get("company"),
                    "role_id": ctx.ref("roles", a.get("role_id")),
                    "notes": a.get("notes"),
                    "created_at": ctx.at(a.get("created_at")),
                }
                for a in data["campaign_activity"]
            ],
        ),
        (
            table_of(jobs.CampaignTaskDone),
            [
                {
                    "user_id": user,
                    "task_id": t["task_id"],
                    "week": t["week"],
                    "done_at": ctx.at(t["done_at"]),
                }
                for t in data["campaign_tasks_done"]
            ],
        ),
        (
            table_of(jobs.Experience),
            [
                {
                    "id": ctx.key("experience", e["id"]),
                    "user_id": user,
                    "requirement": e["requirement"],
                    "role_id": ctx.ref("roles", e.get("role_id")),
                    "created_at": ctx.at(e.get("created_at")),
                    "updated_at": ctx.at(e.get("updated_at")),
                }
                for e in data["experience"]
            ],
        ),
        (
            table_of(jobs.ExperienceEntry),
            [
                {
                    "id": ctx.key("experience_entries", e["id"]),
                    "user_id": user,
                    "experience_id": ctx.key("experience", e["experience_id"]),
                    "context": e.get("context"),
                    "detail": e["detail"],
                    "created_at": ctx.at(e.get("created_at")),
                }
                for e in data["experience_entries"]
            ],
        ),
        (
            table_of(platform.LlmCall),
            [
                {**_call_row(ctx, c), "role_id": role_ref(c.get("role_id"))}
                for c in data["llm_calls"]
            ],
        ),
        (table_of(platform.UserSettings), [_settings_row(ctx)]),
        (table_of(jobs.CampaignNote), _anchor_rows(ctx)),
    ]


def _anchor_rows(ctx: SeedContext) -> list[Row]:
    """The seeded anchor date, so a plan that starts relative to it lines up with the seed."""
    if ctx.anchor is None:
        return []
    note = {"user_id": ctx.account.user_id, "key": SEED_ANCHOR_NOTE}
    return [{**note, "notes": ctx.anchor.date().isoformat()}]


def _event_kind(raw: object) -> str:
    kind = EVENT_KINDS.get(str(raw), str(raw))
    return kind if kind in _EVENT_VOCAB else "note"


def _contact_row(ctx: SeedContext, contact: Row) -> Row:
    return {
        "id": ctx.key("contacts", contact["id"]),
        "user_id": ctx.account.user_id,
        "url": contact["url"],
        "first_name": contact.get("first_name"),
        "last_name": contact.get("last_name"),
        "email": contact.get("email"),
        "company": contact.get("company"),
        "company_norm": company_key(contact.get("company")),
        "position": contact.get("position"),
        "connected_on": ctx.day(contact.get("connected_on")),
        "au_company": contact.get("au_company"),
        "outreach_score": contact.get("outreach_score"),
        "closeness": contact.get("closeness"),
        "message": contact.get("message"),
        "status": contact.get("status") or "new",
        "sent_at": ctx.at(contact.get("sent_at")),
    }


def _target_row(ctx: SeedContext, target: Row) -> Row:
    return {
        "id": ctx.key("targets", target["id"]),
        "user_id": ctx.account.user_id,
        "name": target["name"],
        "name_key": company_key(target["name"]) or str(target["id"]),
        "lane": target["lane"],
        "why": target.get("why"),
        "approach": target.get("approach"),
        "status": target.get("status") or "suggested",
        "origin": target.get("origin") or "import",
        "notes": f"Sector: {target.get('sector')}. Size: {target.get('size')}.",
        "approached_at": ctx.at(target.get("approached_at")),
        "created_at": ctx.at(target.get("created_at")),
        "updated_at": ctx.at(target.get("updated_at")),
    }


def _call_row(ctx: SeedContext, call: Row) -> Row:
    return {
        "id": ctx.key("llm_calls", call["id"]),
        "user_id": ctx.account.user_id,
        "at": ctx.at(call["at"]),
        "feature": LLM_FEATURES.get(str(call["feature"]), call["feature"]),
        "provider": call.get("provider") or "vertex-claude",
        "model": call["model"],
        "input_tokens": call.get("input_tokens") or 0,
        "output_tokens": call.get("output_tokens") or 0,
        "cost_usd": call.get("cost_usd") or 0,
        "ok": bool(call.get("ok")),
        "role_id": ctx.ref("roles", call.get("role_id")),
    }


def _settings_row(ctx: SeedContext) -> Row:
    personas: list[Row] = ctx.data.get("persona") or [{}]
    persona = personas[0]
    profile = {
        key: persona.get(key)
        for key in ("name", "headline", "location", "email")
        if persona.get(key)
    }
    return {"user_id": ctx.account.user_id, "settings": {"profile": profile}}
