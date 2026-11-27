"""Scheduled jobs: feed replay, digest (open model + fallback), rollup and watchdog, each
with its daily spend cap."""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from datetime import date, timedelta

from app.adapters.fakes.data import InMemoryUsageStore, InMemoryWebEvents
from app.adapters.fakes.devboard import InMemoryLeaseStore
from app.adapters.fakes.system import FakeClock, SequentialIds
from app.adapters.llm.providers.base import (
    Provider,
    ProviderChunk,
    ProviderError,
    ProviderRequest,
    ProviderResponse,
)
from app.adapters.llm.providers.replay import ReplayProvider
from app.adapters.llm.routing import load_routing
from app.adapters.llm.service import LlmService
from app.domain.devboard.leases import LeaseService
from app.domain.devboard.model import Actor, ActorKind
from app.domain.devboard.schedule import JOB_CAPS, JobName
from app.domain.errors import BudgetExceeded
from app.domain.identity import derive_user_id
from app.domain.ports import LlmFeature, StoredWebEvent
from app.jobs import digest, feed_replay, rollup, watchdog
from app.main import Container
from tests.conftest import MAINTAINER

BUSY = digest.Activity(
    events={"created": 3, "moved": 5},
    runs={"failed": 1, "succeeded": 2},
    columns={"backlog": 1, "triaged": 2},
)


class StaticActivity:
    def __init__(self, activity: digest.Activity) -> None:
        self._activity = activity
        self.asked: list[tuple[uuid.UUID, object]] = []

    async def since(self, user_id: uuid.UUID, since: object) -> digest.Activity:
        self.asked.append((user_id, since))
        return self._activity


class CountingScorer:
    def __init__(self, fail_after: int | None = None) -> None:
        self.scored: list[uuid.UUID] = []
        self.owners: set[uuid.UUID] = set()
        self._fail_after = fail_after

    async def score_role(self, user_id: uuid.UUID, role_id: uuid.UUID) -> None:
        if self._fail_after is not None and len(self.scored) >= self._fail_after:
            raise BudgetExceeded("Today's AI budget for this feature is used up.")
        self.scored.append(role_id)
        self.owners.add(user_id)


def _held(clock: FakeClock, count: int) -> feed_replay.InMemoryHeldBackRoles:
    user = derive_user_id(MAINTAINER)
    roles = [
        (feed_replay.ReleasedRole(user, uuid.UUID(int=n + 1)), clock.now() - timedelta(minutes=n))
        for n in range(count)
    ]
    roles.append(
        (feed_replay.ReleasedRole(user, uuid.UUID(int=999)), clock.now() + timedelta(days=1))
    )
    return feed_replay.InMemoryHeldBackRoles(held=roles)


async def test_feed_replay_releases_at_most_20_due_roles_and_scores_them(
    container: Container, clock: FakeClock
) -> None:
    held, scorer = _held(clock, 25), CountingScorer()
    report = await feed_replay.run(container, roles=held, scorer=scorer)
    assert report.ok
    assert report.items_new == 20
    assert len(scorer.scored) == 20
    assert uuid.UUID(int=999) not in scorer.scored  # not due yet
    assert len(held.held) == 6


async def test_feed_replay_without_a_scorer_just_releases(
    container: Container, clock: FakeClock
) -> None:
    report = await feed_replay.run(container, roles=_held(clock, 3))
    assert report.items_new == 3
    assert any("without scores" in note for note in report.notes)


async def test_feed_replay_stops_scoring_at_the_cap(container: Container, clock: FakeClock) -> None:
    usage = container.usage
    assert isinstance(usage, InMemoryUsageStore)
    await usage.add_spend(
        clock.now().date(), LlmFeature.SCORE, JOB_CAPS[JobName.FEED_REPLAY].daily_usd
    )
    scorer = CountingScorer()
    report = await feed_replay.run(container, roles=_held(clock, 5), scorer=scorer)
    assert report.items_new == 5
    assert scorer.scored == []
    assert any("spend cap" in note for note in report.notes)


async def test_feed_replay_stops_when_the_model_budget_runs_out(
    container: Container, clock: FakeClock
) -> None:
    report = await feed_replay.run(container, roles=_held(clock, 5), scorer=CountingScorer(2))
    assert report.ok
    assert "Scored 2 roles." in report.notes


async def test_digest_uses_the_open_model_route(container: Container) -> None:
    store = digest.InMemoryDigestStore()
    report = await digest.run(container, activity=StaticActivity(BUSY), store=store)
    assert report.ok
    saved = store.values[digest.STATE_KEY]
    assert saved["headline"] == "3 new cards and 5 moves in the last day."
    usage = container.usage
    assert isinstance(usage, InMemoryUsageStore)
    assert [call.model for call in usage.calls] == ["openai/gpt-oss-120b-maas"]


class DownProvider:
    name = "vertex-maas"

    def __init__(self) -> None:
        self.requests: list[ProviderRequest] = []

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        self.requests.append(request)
        raise ProviderError("unavailable")

    async def stream(self, request: ProviderRequest) -> AsyncGenerator[ProviderChunk]:
        self.requests.append(request)
        raise ProviderError("unavailable")
        yield ProviderChunk()


async def test_digest_falls_back_to_claude_when_the_open_model_fails(container: Container) -> None:
    settings = container.settings
    replay = ReplayProvider(settings.llm_fixtures_dir / "devboard")
    providers: dict[str, Provider] = {"vertex-maas": DownProvider(), "vertex-claude": replay}
    llm = LlmService(
        load_routing(settings.models_config),
        providers,
        container.guard,
        container.pii,
        container.usage,
        container.clock,
    )
    store = digest.InMemoryDigestStore()
    report = await digest.run(container, activity=StaticActivity(BUSY), store=store, llm=llm)
    assert report.ok
    assert any("(fallback)" in note for note in report.notes)
    usage = container.usage
    assert isinstance(usage, InMemoryUsageStore)
    assert [call.is_fallback for call in usage.calls] == [False, True]


async def test_digest_offline_without_a_recording_stores_the_template(container: Container) -> None:
    store = digest.InMemoryDigestStore()
    odd = digest.Activity(events={"created": 42})
    report = await digest.run(container, activity=StaticActivity(odd), store=store)
    assert report.ok
    assert store.values[digest.STATE_KEY]["provider"] == "template"


async def test_digest_respects_its_cap(container: Container, clock: FakeClock) -> None:
    usage = container.usage
    assert isinstance(usage, InMemoryUsageStore)
    await usage.add_spend(clock.now().date(), LlmFeature.DIGEST, 1.0)
    store = digest.InMemoryDigestStore()
    report = await digest.run(container, activity=StaticActivity(BUSY), store=store)
    assert store.values[digest.STATE_KEY]["provider"] == "template"
    assert usage.calls == []
    assert any("spend cap" in note for note in report.notes)


def _event(day: date) -> StoredWebEvent:
    return StoredWebEvent(
        at=FakeClock().now(), day=day, session_id="s", role_tag="user", type="page_view", route="/"
    )


async def test_rollup_purges_web_events_older_than_90_days(
    container: Container, clock: FakeClock
) -> None:
    events = container.web_events
    assert isinstance(events, InMemoryWebEvents)
    today = clock.now().date()
    await events.insert_many(
        [_event(today - timedelta(days=91)), _event(today - timedelta(days=89)), _event(today)]
    )
    report = await rollup.run(container)
    assert report.ok
    assert report.items_seen == 1
    assert [event.day for event in events.events] == [today - timedelta(days=89), today]


async def test_watchdog_reclaims_expired_leases(container: Container, clock: FakeClock) -> None:
    leases = LeaseService(InMemoryLeaseStore(SequentialIds()), clock)
    actor = Actor(derive_user_id(MAINTAINER), ActorKind.AGENT)
    await leases.claim(actor, uuid.UUID(int=7), uuid.UUID(int=8), ("web/src/features/a/",))
    await leases.claim(
        actor, uuid.UUID(int=9), uuid.UUID(int=10), ("web/src/features/b/",), ttl=timedelta(hours=1)
    )
    clock.advance(timedelta(minutes=30))
    report = await watchdog.run(container, leases=leases)
    assert report.ok
    assert report.items_new == 1
    assert [lease.path for lease in await leases.active(actor)] == ["web/src/features/b/"]
    assert any("2 provisioned accounts" in note for note in report.notes)


async def test_watchdog_alerts_on_an_unexpected_account(container: Container) -> None:
    report = await watchdog.run(container, directory=watchdog.StaticIdentityDirectory(3))
    assert not report.ok
    assert report.error_code == "unexpected_user_count"
