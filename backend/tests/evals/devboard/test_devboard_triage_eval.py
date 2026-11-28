"""Triage eval on a labelled set (nightly; offline it replays the recorded answers).

Each label is the column and risk the board must end with. The score is the share of cards
that match; the gate is 100% on this small set because code, not the model, decides scope and
risk once the model has answered.
"""

from __future__ import annotations

import pytest
from tests.conftest import MAINTAINER
from tests.unit.devboard import cards

from app.compose.devboard import build
from app.domain.devboard.model import Actor, ActorKind, Column, Risk
from app.domain.identity import AppRole, derive_user_id
from app.main import Container

pytestmark = pytest.mark.evals

LABELS = (
    (cards.API_SEARCH, Column.TRIAGED, Risk.MEDIUM),
    (cards.INFRA_BACKUP, Column.OUT_OF_SCOPE, Risk.MEDIUM),
    (cards.TEST_PLUGIN, Column.OUT_OF_SCOPE, Risk.LOW),
    (cards.RUN_COST, Column.TRIAGED, Risk.MEDIUM),
)
GATE = 1.0


async def test_triage_matches_the_labelled_set(container: Container) -> None:
    runtime = build(container)
    actor = Actor(derive_user_id(MAINTAINER), ActorKind.MAINTAINER, AppRole.MAINTAINER)
    hits = 0
    for card, column, risk in LABELS:
        task = await runtime.services.board.create(actor, card.title, card.body, ())
        await runtime.dispatcher.drain()
        result = await runtime.services.board.get(actor, task.id)
        hits += int(result.column is column and result.risk is risk)
    assert hits / len(LABELS) >= GATE
