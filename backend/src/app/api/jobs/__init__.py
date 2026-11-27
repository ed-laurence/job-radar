"""Job-search routes: roles, contacts, targets, kits, chat, research, the dashboard,
rejections and follow-ups."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.jobs import (
    campaign,
    crm,
    dashboard,
    evidence,
    kit_files,
    profile,
    rejections,
    roles,
    streams,
)


def routers() -> tuple[APIRouter, ...]:
    return (
        roles.router,
        crm.router,
        streams.router,
        dashboard.router,
        rejections.router,
        campaign.router,
        profile.router,
        evidence.router,
        kit_files.router,
    )
