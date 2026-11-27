"""Per-user settings (spec tag Settings), stored as one `jsonb` document in `user_settings`.

Every value has a default, so a user with no row (or an older document missing a section) reads
the defaults. Writes replace the whole document under `version` (409 on a stale write) and keep
keys this module doesn't own (the seeded profile's `name`, `headline`, `location`).

`llm.monthly_budget_aud` is applied only for maintainers: a non-maintainer's value is ignored and
the stored one kept. When it was never set, the deployment's `LLM_BUDGET_MONTH_AUD` applies. It
is AUD, compared with spend converted from the providers' USD (`app.domain.money`). A document
written before the AUD switch holds `monthly_budget_usd`; that value is read as AUD (a slightly
tighter cap, never a looser one) and saved under the new key on the next write.
Job scraping settings are deliberately not here.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from functools import cache
from typing import Protocol, cast
from zoneinfo import available_timezones

from app.domain.errors import BudgetExceeded, FieldError, ValidationFailed
from app.domain.health import month_start
from app.domain.identity import Principal
from app.domain.money import DEFAULT_USD_TO_AUD, to_aud
from app.domain.ports.health import HealthSource

type JsonMap = Mapping[str, object]

SPELLINGS = ("en-AU", "en-GB", "en-US")


@dataclass(frozen=True, kw_only=True)
class ScoringPrefs:
    keep_score: int = 55
    borderline_score: int = 45
    notify_score: int = 75
    notify_mission_score: int = 65

    @property
    def filter_below(self) -> int:
        """Roles below this (and not a mission organisation) are filtered out."""
        return min(self.borderline_score, self.keep_score)


WORK_MODES = ("remote", "hybrid", "on_site")
MAX_PREFERRED_LOCATIONS = 10


@dataclass(frozen=True, kw_only=True)
class ScoreWeights:
    """Points per part of the score (job-seeker's caps by default); they sum to 100."""

    skills: int = 50
    track_pay: int = 25
    location: int = 15
    freshness: int = 10

    def as_caps(self) -> dict[str, int]:
        return {
            "skills": self.skills,
            "track_pay": self.track_pay,
            "location": self.location,
            "freshness": self.freshness,
        }


@dataclass(frozen=True, kw_only=True)
class ScoringPreferences:
    weights: ScoreWeights = field(default_factory=ScoreWeights)
    perm_salary_aud: int | None = None
    contract_day_rate_aud: int | None = None
    work_modes: tuple[str, ...] = ()
    preferred_locations: tuple[str, ...] = ()

    @property
    def has_targets(self) -> bool:
        """Pay floors, work modes or locations were set (they reach the scoring prompt)."""
        return bool(
            self.perm_salary_aud is not None
            or self.contract_day_rate_aud is not None
            or self.work_modes
            or self.preferred_locations
        )


@dataclass(frozen=True, kw_only=True)
class FollowupPrefs:
    after_hours: int = 24
    due_hours: int = 48
    nudge_days: int = 7

    @property
    def after(self) -> timedelta:
        """A follow-up is suggested (and counted as due on the main page) this long after."""
        return timedelta(hours=self.after_hours)

    @property
    def due(self) -> timedelta:
        return timedelta(hours=self.due_hours)


@dataclass(frozen=True, kw_only=True)
class KitPrefs:
    auto_kits: bool = False
    kits_per_run: int = 5
    spelling: str = "en-AU"


@dataclass(frozen=True, kw_only=True)
class UserPrefs:
    skills: str = ""
    brief: str = ""
    scoring: ScoringPrefs = field(default_factory=ScoringPrefs)
    scoring_preferences: ScoringPreferences = field(default_factory=ScoringPreferences)
    followups: FollowupPrefs = field(default_factory=FollowupPrefs)
    kits: KitPrefs = field(default_factory=KitPrefs)
    campaign_start: date | None = None
    archive_default_days: int = 14
    monthly_budget_aud: float | None = None
    timezone: str = "Australia/Melbourne"
    version: int = 1
    updated_at: datetime | None = None


DEFAULTS = UserPrefs()
DEFAULT_WEIGHTS = ScoreWeights()


@dataclass(frozen=True, kw_only=True)
class StoredSettings:
    data: JsonMap
    version: int
    updated_at: datetime | None = None


class UserSettingsStore(Protocol):
    async def load(self, user_id: uuid.UUID) -> StoredSettings | None: ...

    async def save(self, user_id: uuid.UUID, data: JsonMap, version: int) -> StoredSettings:
        """Replace the document if `version` is current (a missing row is version 1).
        Raises `Conflict` when it is stale."""
        ...


def _section(data: JsonMap, key: str) -> JsonMap:
    raw = data.get(key)
    return cast("JsonMap", raw) if isinstance(raw, Mapping) else {}


def _int(section: JsonMap, key: str, default: int) -> int:
    value = section.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def _str(section: JsonMap, key: str, default: str) -> str:
    value = section.get(key)
    return value if isinstance(value, str) else default


BUDGET_KEY = "monthly_budget_aud"
LEGACY_BUDGET_KEY = "monthly_budget_usd"


def _budget(llm: JsonMap, default_budget: float | None) -> object:
    """The stored budget (AUD). A pre-AUD document's `monthly_budget_usd` is read as AUD."""
    if BUDGET_KEY in llm:
        return llm[BUDGET_KEY]
    return llm.get(LEGACY_BUDGET_KEY, default_budget)


def parse(stored: StoredSettings | None, default_budget: float | None = None) -> UserPrefs:
    """The effective settings from a stored document (missing keys read as defaults)."""
    data: JsonMap = stored.data if stored is not None else {}
    profile, scoring = _section(data, "profile"), _section(data, "scoring")
    followups, kits = _section(data, "followups"), _section(data, "kits")
    llm = _section(data, "llm")
    budget = _budget(llm, default_budget) if "llm" in data else default_budget
    raw_start = _section(data, "campaign").get("start_date")
    spelling = _str(kits, "spelling", DEFAULTS.kits.spelling)
    return UserPrefs(
        skills=_str(profile, "skills", ""),
        brief=_str(profile, "brief", ""),
        scoring=ScoringPrefs(
            keep_score=_int(scoring, "keep_score", DEFAULTS.scoring.keep_score),
            borderline_score=_int(scoring, "borderline_score", DEFAULTS.scoring.borderline_score),
            notify_score=_int(scoring, "notify_score", DEFAULTS.scoring.notify_score),
            notify_mission_score=_int(
                scoring, "notify_mission_score", DEFAULTS.scoring.notify_mission_score
            ),
        ),
        scoring_preferences=_scoring_preferences(_section(data, "scoring_preferences")),
        followups=FollowupPrefs(
            after_hours=_int(followups, "after_hours", DEFAULTS.followups.after_hours),
            due_hours=_int(followups, "due_hours", DEFAULTS.followups.due_hours),
            nudge_days=_int(followups, "nudge_days", DEFAULTS.followups.nudge_days),
        ),
        kits=KitPrefs(
            auto_kits=kits.get("auto_kits") is True,
            kits_per_run=_int(kits, "kits_per_run", DEFAULTS.kits.kits_per_run),
            spelling=spelling if spelling in SPELLINGS else DEFAULTS.kits.spelling,
        ),
        campaign_start=_date(raw_start),
        archive_default_days=_int(data, "archive_default_days", DEFAULTS.archive_default_days),
        monthly_budget_aud=float(budget) if isinstance(budget, int | float) else None,
        timezone=_str(_section(data, "display"), "timezone", DEFAULTS.timezone),
        version=stored.version if stored is not None else 1,
        updated_at=stored.updated_at if stored is not None else None,
    )


def _optional_int(section: JsonMap, key: str) -> int | None:
    value = section.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _strings(section: JsonMap, key: str) -> tuple[str, ...]:
    raw = section.get(key)
    if not isinstance(raw, list):
        return ()
    return tuple(item for item in cast("list[object]", raw) if isinstance(item, str))


def _scoring_preferences(section: JsonMap) -> ScoringPreferences:
    """Stored preferences; weights that don't sum to 100 read as the defaults."""
    raw = _section(section, "weights")
    default = DEFAULT_WEIGHTS
    weights = ScoreWeights(
        skills=_int(raw, "skills", default.skills),
        track_pay=_int(raw, "track_pay", default.track_pay),
        location=_int(raw, "location", default.location),
        freshness=_int(raw, "freshness", default.freshness),
    )
    if sum(weights.as_caps().values()) != 100 or min(weights.as_caps().values()) < 0:
        weights = default
    floors = _section(section, "pay_floors")
    return ScoringPreferences(
        weights=weights,
        perm_salary_aud=_optional_int(floors, "perm_salary_aud"),
        contract_day_rate_aud=_optional_int(floors, "contract_day_rate_aud"),
        work_modes=tuple(m for m in _strings(section, "work_modes") if m in WORK_MODES),
        preferred_locations=_strings(section, "preferred_locations")[:MAX_PREFERRED_LOCATIONS],
    )


def _scoring_preferences_document(prefs: ScoringPreferences) -> dict[str, object]:
    return {
        "weights": prefs.weights.as_caps(),
        "pay_floors": {
            "perm_salary_aud": prefs.perm_salary_aud,
            "contract_day_rate_aud": prefs.contract_day_rate_aud,
        },
        "work_modes": list(prefs.work_modes),
        "preferred_locations": list(prefs.preferred_locations),
    }


def _date(raw: object) -> date | None:
    if isinstance(raw, date):
        return raw
    if isinstance(raw, str) and raw:
        try:
            return date.fromisoformat(raw[:10])
        except ValueError:
            return None
    return None


def _document(prefs: UserPrefs, previous: JsonMap) -> dict[str, object]:
    """The stored document: these settings over the previous one (unknown keys kept)."""
    document = dict(previous)
    document["profile"] = {
        **_section(previous, "profile"),
        "skills": prefs.skills,
        "brief": prefs.brief,
    }
    document["scoring"] = {
        "keep_score": prefs.scoring.keep_score,
        "borderline_score": prefs.scoring.borderline_score,
        "notify_score": prefs.scoring.notify_score,
        "notify_mission_score": prefs.scoring.notify_mission_score,
    }
    document["scoring_preferences"] = _scoring_preferences_document(prefs.scoring_preferences)
    document["followups"] = {
        "after_hours": prefs.followups.after_hours,
        "due_hours": prefs.followups.due_hours,
        "nudge_days": prefs.followups.nudge_days,
    }
    document["kits"] = {
        "auto_kits": prefs.kits.auto_kits,
        "kits_per_run": prefs.kits.kits_per_run,
        "spelling": prefs.kits.spelling,
    }
    start = prefs.campaign_start
    document["campaign"] = {"start_date": start.isoformat() if start else None}
    document["archive_default_days"] = prefs.archive_default_days
    document["display"] = {"timezone": prefs.timezone}
    return document


_ZONE = re.compile(r"^(?:UTC|[A-Z][A-Za-z_]+(?:/[A-Za-z0-9_+-]+){1,2})$")


@cache
def _known_zones() -> frozenset[str]:
    return frozenset(available_timezones())


def is_time_zone(name: str) -> bool:
    """An IANA zone name. Where the platform has no zone database (Windows without `tzdata`),
    only the name's shape is checked."""
    known = _known_zones()
    return name in known if known else bool(_ZONE.match(name))


def _check(prefs: UserPrefs) -> None:
    errors: list[FieldError] = []
    if prefs.scoring.borderline_score > prefs.scoring.keep_score:
        errors.append(FieldError("/scoring/borderline_score", "Must not be above the keep score."))
    if prefs.followups.due_hours < prefs.followups.after_hours:
        errors.append(
            FieldError("/followups/due_hours", "Must not be before the follow-up suggestion.")
        )
    if prefs.kits.spelling not in SPELLINGS:
        errors.append(FieldError("/kits/spelling", "Pick en-AU, en-GB or en-US."))
    if not is_time_zone(prefs.timezone):
        errors.append(FieldError("/display/timezone", "Use an IANA time zone."))
    errors.extend(check_scoring_preferences(prefs.scoring_preferences))
    if errors:
        raise ValidationFailed("Check the highlighted settings.", errors=errors)


def check_scoring_preferences(prefs: ScoringPreferences) -> list[FieldError]:
    errors: list[FieldError] = []
    caps = prefs.weights.as_caps()
    if any(value < 0 or value > 100 for value in caps.values()):
        errors.append(FieldError("/scoring_preferences/weights", "Each weight is 0 to 100."))
    elif sum(caps.values()) != 100:
        errors.append(FieldError("/scoring_preferences/weights", "The weights must add up to 100."))
    if any(mode not in WORK_MODES for mode in prefs.work_modes):
        errors.append(
            FieldError("/scoring_preferences/work_modes", "Pick remote, hybrid or on_site.")
        )
    if len(set(prefs.work_modes)) != len(prefs.work_modes):
        errors.append(FieldError("/scoring_preferences/work_modes", "List each mode once."))
    places = prefs.preferred_locations
    if len(places) > MAX_PREFERRED_LOCATIONS or any(not p.strip() for p in places):
        errors.append(
            FieldError("/scoring_preferences/preferred_locations", "Up to 10 non-empty places.")
        )
    for key, value in (
        ("perm_salary_aud", prefs.perm_salary_aud),
        ("contract_day_rate_aud", prefs.contract_day_rate_aud),
    ):
        if value is not None and value < 0:
            errors.append(
                FieldError(f"/scoring_preferences/pay_floors/{key}", "Must be 0 or more.")
            )
    return errors


class PrefsSource(Protocol):
    """What other areas read: the user's effective settings (`SettingsService` is one)."""

    async def for_user(self, user_id: uuid.UUID) -> UserPrefs: ...


async def prefs_for(source: PrefsSource | None, user_id: uuid.UUID) -> UserPrefs:
    return DEFAULTS if source is None else await source.for_user(user_id)


class SettingsService:
    def __init__(self, store: UserSettingsStore, default_budget: float | None = None) -> None:
        self._store = store
        self._default_budget = default_budget

    async def for_user(self, user_id: uuid.UUID) -> UserPrefs:
        return parse(await self._store.load(user_id), self._default_budget)

    async def get(self, principal: Principal) -> UserPrefs:
        return await self.for_user(principal.user_id)

    async def update(
        self, principal: Principal, wanted: UserPrefs, *, keep_scoring_preferences: bool = False
    ) -> UserPrefs:
        """Replace the caller's settings. `wanted.version` is the version they read. With
        `keep_scoring_preferences` (the update omitted them) the stored ones are kept."""
        stored = await self._store.load(principal.user_id)
        current = parse(stored, self._default_budget)
        if keep_scoring_preferences:
            wanted = replace(wanted, scoring_preferences=current.scoring_preferences)
        _check(wanted)
        previous: JsonMap = stored.data if stored is not None else {}
        document = _document(wanted, previous)
        if principal.is_maintainer:
            document["llm"] = {BUDGET_KEY: wanted.monthly_budget_aud}
        elif "llm" in previous:
            document["llm"] = previous["llm"]
        saved = await self._store.save(principal.user_id, document, wanted.version)
        result = parse(saved, self._default_budget)
        if not principal.is_maintainer:
            result = replace(result, monthly_budget_aud=current.monthly_budget_aud)
        return result

    async def monthly_budget(self, user_id: uuid.UUID) -> float | None:
        return (await self.for_user(user_id)).monthly_budget_aud


class MonthlySpendCap:
    """The LLM spend cap from `llm.monthly_budget_aud`: once the user's spend this calendar month
    (UTC), converted to AUD, reaches it, further calls fail with `BudgetExceeded`. No budget
    means no cap."""

    def __init__(
        self,
        settings: SettingsService,
        calls: HealthSource,
        usd_to_aud: float = DEFAULT_USD_TO_AUD,
    ) -> None:
        self._settings = settings
        self._calls = calls
        self._usd_to_aud = usd_to_aud

    async def check(self, user_id: uuid.UUID, now: datetime) -> None:
        budget = await self._settings.monthly_budget(user_id)
        if budget is None:
            return
        calls = await self._calls.llm_calls(user_id, month_start(now))
        spent_usd = sum(call.cost_usd for call in calls if call.at <= now)
        if to_aud(spent_usd, self._usd_to_aud) >= budget:
            raise BudgetExceeded("This month's AI budget is used up.")
