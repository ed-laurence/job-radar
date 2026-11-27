"""The caller's settings (spec tag Settings)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.api.security import rate_limited_user
from app.domain.identity import Principal
from app.domain.rate_limits import RateTier
from app.domain.user_settings import (
    FollowupPrefs,
    KitPrefs,
    ScoreWeights,
    ScoringPrefs,
    SettingsService,
    UserPrefs,
)
from app.domain.user_settings import ScoringPreferences as ScoringPreferencesPrefs
from app.generated.models import (
    Campaign,
    Display,
    Followups,
    Kits,
    Llm1,
    PayFloors,
    Profile,
    Scoring,
    ScoringPreferences,
    UserSettings,
    UserSettingsUpdate,
    Weights,
)

router = APIRouter(prefix="/api", tags=["Settings"])

User = Annotated[Principal, Depends(rate_limited_user(RateTier.DEFAULT))]


def _settings(request: Request) -> SettingsService:
    found = request.app.state.user_settings
    assert isinstance(found, SettingsService)
    return found


@router.get("/settings", operation_id="getSettings", response_model=UserSettings)
async def get_settings(request: Request, principal: User) -> UserSettings:
    return to_model(await _settings(request).get(principal))


@router.put("/settings", operation_id="updateSettings", response_model=UserSettings)
async def update_settings(
    request: Request, principal: User, body: UserSettingsUpdate
) -> UserSettings:
    keep = body.scoring_preferences is None
    saved = await _settings(request).update(
        principal, from_model(body), keep_scoring_preferences=keep
    )
    return to_model(saved)


def from_model(body: UserSettingsUpdate) -> UserPrefs:
    return UserPrefs(
        skills=body.profile.skills,
        brief=body.profile.brief,
        scoring=ScoringPrefs(**body.scoring.model_dump()),
        scoring_preferences=_preferences_from(body.scoring_preferences),
        followups=FollowupPrefs(**body.followups.model_dump()),
        kits=KitPrefs(
            auto_kits=body.kits.auto_kits,
            kits_per_run=body.kits.kits_per_run,
            spelling=body.kits.spelling.value,
        ),
        campaign_start=body.campaign.start_date,
        archive_default_days=body.archive_default_days,
        monthly_budget_aud=body.llm.monthly_budget_aud,
        timezone=body.display.timezone,
        version=body.version,
    )


def _preferences_from(body: ScoringPreferences | None) -> ScoringPreferencesPrefs:
    if body is None:
        return ScoringPreferencesPrefs()
    return ScoringPreferencesPrefs(
        weights=ScoreWeights(**body.weights.model_dump()),
        perm_salary_aud=body.pay_floors.perm_salary_aud,
        contract_day_rate_aud=body.pay_floors.contract_day_rate_aud,
        work_modes=tuple(mode.value for mode in body.work_modes),
        preferred_locations=tuple(place.root for place in body.preferred_locations),
    )


def _preferences_to(prefs: ScoringPreferencesPrefs) -> ScoringPreferences:
    return ScoringPreferences.model_validate(
        {
            "weights": Weights(**prefs.weights.as_caps()),
            "pay_floors": PayFloors(
                perm_salary_aud=prefs.perm_salary_aud,
                contract_day_rate_aud=prefs.contract_day_rate_aud,
            ),
            "work_modes": list(prefs.work_modes),
            "preferred_locations": list(prefs.preferred_locations),
        }
    )


def to_model(prefs: UserPrefs) -> UserSettings:
    return UserSettings(
        profile=Profile(skills=prefs.skills, brief=prefs.brief),
        scoring=Scoring.model_validate(prefs.scoring, from_attributes=True),
        scoring_preferences=_preferences_to(prefs.scoring_preferences),
        followups=Followups.model_validate(prefs.followups, from_attributes=True),
        kits=Kits.model_validate(
            {
                "auto_kits": prefs.kits.auto_kits,
                "kits_per_run": prefs.kits.kits_per_run,
                "spelling": prefs.kits.spelling,
            }
        ),
        campaign=Campaign(start_date=prefs.campaign_start),
        archive_default_days=prefs.archive_default_days,
        llm=Llm1(monthly_budget_aud=prefs.monthly_budget_aud),
        display=Display(timezone=prefs.timezone),
        version=prefs.version,
        updated_at=prefs.updated_at,
    )
