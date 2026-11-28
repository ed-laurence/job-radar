"""Fixed vocabularies for CHECK constraints. They mirror the spec enums (and data-model.md where
the spec is silent). Changing one needs a migration."""

from __future__ import annotations

APP_ROLE = ("maintainer", "user")

ROLE_STATUS = (
    "new",
    "reviewing",
    "applied",
    "interview",
    "offer",
    "passed",
    "closed",
    "rejected",
    "unknown",
)
ROLE_STAGE = ("new", "filtered", "screened", "scored", "ready", "duplicate", "error")
WORK_MODE = ("remote", "hybrid", "on_site")
TRACK = ("contract", "exec", "mission")
ROLE_SOURCE = ("feed", "manual", "link", "seed")
ROLE_EVENT_KIND = (
    "status",
    "note",
    "kit_regenerated",
    "kit_edit",
    "followed_up",
    "scored",
    "rank",
    "notified",
    "rating",
)
CHAT_AUTHOR = ("user", "assistant")
TASK_CHAT_PROPOSAL_STATUS = ("pending", "accepted", "rejected")
FILE_KIND = ("image", "pdf", "text")
REJECTION_CATEGORY = (
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
)
CONTACT_STATUS = ("new", "drafted", "sent", "replied", "skipped")
CLOSENESS = ("worked", "know", "barely")
TARGET_STATUS = ("suggested", "approved", "dismissed", "approached", "replied")
TARGET_LANE = ("A", "B", "C")
TARGET_ORIGIN = ("ai", "import", "manual")
RESEARCH_FOCUS = ("overview", "hiring_signals", "people_to_approach", "news")

RUN_STATUS = ("queued", "running", "succeeded", "failed", "killed", "timed_out")
STEP_KIND = (
    "thought",
    "tool_call",
    "tool_result",
    "message",
    "handoff",
    "lease",
    "status",
    "finding",
)
TASK_COLUMN = (
    "backlog",
    "triaged",
    "out_of_scope",
    "plan_review",
    "ready",
    "agent_dev",
    "agent_review",
    "changes_requested",
    "human_review",
    "blocked",
    "done",
)
TASK_TYPE = (
    "feature",
    "bug",
    "security",
    "performance",
    "chore",
    "refactor",
    "test",
    "docs",
)
TASK_PRIORITY = ("P0", "P1", "P2", "P3")
TASK_SEVERITY = ("critical", "major", "minor", "trivial")
TRIAGE_ROUTE = ("now", "next", "later")
TRIAGE_VERIFICATION = ("confirmed", "not_reproduced", "unclear", "duplicate")
TASK_SIZE = ("S", "M", "L")
TASK_RISK = ("low", "medium", "high")
TASK_AREA = ("web", "api")
TASK_EVENT_KIND = (
    "created",
    "edited",
    "moved",
    "triaged",
    "planned",
    "approved",
    "run_started",
    "run_finished",
    "killed",
    "pii_redacted",
)
ACTOR_KIND = ("maintainer", "user", "agent", "system")
RUN_KIND = ("triage", "plan", "test_writer", "dev", "publish", "review")
REVIEW_VERDICT = ("approved", "changes_requested", "needs_decision")
PLAN_MODE = ("single", "multi")
LEASE_KIND = ("exclusive", "hotspot")

LLM_FEATURE = (
    "score",
    "extract",
    "kit",
    "chat",
    "research",
    "triage",
    "plan",
    "dev",
    "review",
    "digest",
    "followup",
    "outreach",
    "rejection_analysis",
    "eval",
    "evidence",
    "task_chat",
)
LLM_PROVIDER = ("vertex-claude", "vertex-gemini", "vertex-maas", "replay")
GUARD_RESULT = ("pass", "block", "redact")

WEB_EVENT_TYPE = ("page_view", "product", "web_vital")
WEB_EVENT_NAME = (
    "kit_generated",
    "chat_sent",
    "role_created",
    "role_moved",
    "card_moved",
    "agent_run_started",
    "agent_run_approved",
    "research_started",
)
WEB_VITAL_METRIC = ("LCP", "INP", "CLS", "FCP", "TTFB")
WEB_VITAL_RATING = ("good", "needs-improvement", "poor")
JOB_NAME = ("scan", "feed-replay", "digest", "rollup", "watchdog", "evals", "seed", "tester-reset")
RATE_TIER = ("public", "default", "events", "stream", "ai", "agent")
