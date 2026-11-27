"""Every table, plus the tenancy classification the migration uses for RLS policies."""

from __future__ import annotations

from app.adapters.db import (
    tables_board,
    tables_jobs,
    tables_platform,
    tables_research,
    tables_scan,
    tables_task_chat,
)
from app.adapters.db.base import Base

# User-owned (U): `user_id = current_setting('app.user_id')::uuid` for every command.
USER_OWNED_TABLES = (
    "roles",
    "role_kits",
    "role_events",
    "role_chats",
    "role_chat_files",
    "rejections",
    "followups",
    "role_recruiters",
    "contacts",
    "targets",
    "research_runs",
    "campaign_activity",
    "campaign_task_done",
    "campaign_notes",
    "experience",
    "experience_entries",
    "user_settings",
    "llm_calls",
    "role_pool",
    "profile_documents",
    "role_evidence",
    "task_chat_messages",
    "task_chat_proposals",
)

# Shared (S): visible to both accounts, but only with `app.user_id` set.
SHARED_TABLES = (
    "dev_tasks",
    "task_events",
    "task_triage",
    "task_plans",
    "runs",
    # Mixed: board steps (user_id NULL) are shared; research steps are owned by user_id.
    "run_steps",
    "leases",
    "lease_waits",
)

# System (Sys): written by the app or jobs only, never returned raw. `users` has its own policy.
SYSTEM_TABLES = (
    "llm_budget_usage",
    "rate_limit_buckets",
    "web_events",
    "job_runs",
    "app_state",
    "scan_runs",
)

metadata = Base.metadata

__all__ = [
    "SHARED_TABLES",
    "SYSTEM_TABLES",
    "USER_OWNED_TABLES",
    "Base",
    "metadata",
    "tables_board",
    "tables_jobs",
    "tables_platform",
    "tables_research",
    "tables_scan",
    "tables_task_chat",
]
