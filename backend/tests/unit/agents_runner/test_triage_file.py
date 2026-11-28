"""The strict `/out/triage.json` parser: the sandboxed triager's output is untrusted, so unknown
keys, bad enums, inconsistent fields, oversize files and another task's answer are refused.
Triage writes no acceptance criteria; a file that carries them is refused too."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from app.adapters.agents.triage_file import (
    MAX_TRIAGE_BYTES,
    TriageFileError,
    parse_triage,
    read_triage,
)
from app.domain.devboard.model import (
    Area,
    Priority,
    Risk,
    Route,
    Severity,
    Size,
    TaskType,
    Verification,
    VerificationStatus,
)
from tests.unit.agents_runner.fakes import TASK_ID

DUP = uuid.UUID("00000000-0000-4000-8000-0000000000d1")


def verification(**changes: object) -> dict[str, object]:
    body: dict[str, object] = {
        "status": "confirmed",
        "evidence": "Card.tsx never renders the badge.",
        "code_refs": ["web/src/features/board/Card.tsx"],
        "duplicate_of": None,
    }
    return body | changes


def triage_file(**changes: object) -> dict[str, object]:
    body: dict[str, object] = {
        "task_id": str(TASK_ID),
        "type": "bug",
        "size": "S",
        "risk": "low",
        "areas": ["web"],
        "priority": "P1",
        "severity": "major",
        "impact": "Maintainers can't see priority at a glance.",
        "route": "now",
        "route_reason": "Blocks the board demo.",
        "needs_human_design": False,
        "out_of_scope": False,
        "out_of_scope_reason": None,
        "likely_paths": ["web/src/features/board/Card.tsx"],
        "verification": verification(),
    }
    return body | changes


def _raw(body: dict[str, object]) -> bytes:
    return json.dumps(body).encode()


def test_a_valid_triage_becomes_a_decision() -> None:
    decision = parse_triage(_raw(triage_file()), str(TASK_ID))
    assert (decision.type, decision.size, decision.llm_risk) == (TaskType.BUG, Size.S, Risk.LOW)
    assert decision.areas == (Area.WEB,)
    assert (decision.priority, decision.severity, decision.route) == (
        Priority.P1,
        Severity.MAJOR,
        Route.NOW,
    )
    assert decision.impact == "Maintainers can't see priority at a glance."
    assert decision.route_reason == "Blocks the board demo."
    assert decision.verification == Verification(
        VerificationStatus.CONFIRMED,
        "Card.tsx never renders the badge.",
        ("web/src/features/board/Card.tsx",),
        None,
    )
    assert decision.likely_paths == ("web/src/features/board/Card.tsx",)
    assert decision.out_of_scope_reason is None
    assert decision.needs_human_design is False


def test_a_duplicate_names_its_card() -> None:
    dup = verification(status="duplicate", duplicate_of=str(DUP), code_refs=[])
    decision = parse_triage(_raw(triage_file(verification=dup)), str(TASK_ID))
    assert decision.verification.status is VerificationStatus.DUPLICATE
    assert decision.verification.duplicate_of == DUP


def test_an_out_of_scope_card_keeps_its_reason() -> None:
    body = triage_file(areas=[], out_of_scope=True, out_of_scope_reason="Infra, not web or API.")
    decision = parse_triage(_raw(body), str(TASK_ID))
    assert decision.out_of_scope_reason == "Infra, not web or API."
    assert decision.areas == ()


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(triage_file(extra="x"), id="unknown-key"),
        pytest.param(
            triage_file(acceptance_criteria=[{"test_id": "t.py::x", "expectation": "x"}]),
            id="acceptance-criteria",
        ),
        pytest.param(triage_file(type="epic"), id="bad-type"),
        pytest.param(triage_file(size="XL"), id="bad-size"),
        pytest.param(triage_file(risk="none"), id="bad-risk"),
        pytest.param(triage_file(areas=["db"]), id="bad-area"),
        pytest.param(triage_file(areas=["web", "web"]), id="repeated-area"),
        pytest.param(triage_file(areas=[]), id="in-scope-without-area"),
        pytest.param(triage_file(priority="P4"), id="bad-priority"),
        pytest.param(triage_file(severity="blocker"), id="bad-severity"),
        pytest.param(triage_file(route="soon"), id="bad-route"),
        pytest.param(triage_file(impact=""), id="no-impact"),
        pytest.param(triage_file(impact="x" * 601), id="long-impact"),
        pytest.param(triage_file(route_reason="x" * 401), id="long-route-reason"),
        pytest.param(triage_file(needs_human_design="no"), id="string-bool"),
        pytest.param(triage_file(out_of_scope=True), id="out-of-scope-without-reason"),
        pytest.param(triage_file(out_of_scope_reason="Not ours."), id="reason-in-scope"),
        pytest.param(triage_file(likely_paths=["a.py"] * 31), id="many-paths"),
        pytest.param(triage_file(likely_paths=["a" * 301]), id="long-path"),
        pytest.param(triage_file(verification=verification(colour=1)), id="unknown-verif-key"),
        pytest.param(triage_file(verification=verification(status="fixed")), id="bad-status"),
        pytest.param(triage_file(verification=verification(evidence="")), id="no-evidence"),
        pytest.param(
            triage_file(verification=verification(evidence="x" * 1001)), id="long-evidence"
        ),
        pytest.param(triage_file(verification=verification(code_refs=["a"] * 21)), id="refs"),
        pytest.param(
            triage_file(verification=verification(status="duplicate")), id="dup-without-card"
        ),
        pytest.param(
            triage_file(verification=verification(duplicate_of=str(DUP))), id="card-not-dup"
        ),
        pytest.param(
            triage_file(verification=verification(status="duplicate", duplicate_of="card-1")),
            id="dup-not-uuid",
        ),
        pytest.param({k: v for k, v in triage_file().items() if k != "verification"}, id="missing"),
    ],
)
def test_malformed_triage_is_refused(body: dict[str, object]) -> None:
    with pytest.raises(TriageFileError) as caught:
        parse_triage(_raw(body), str(TASK_ID))
    assert str(caught.value) == "The triager's answer is malformed."


@pytest.mark.parametrize("raw", [b"{not json", b"\xff\xfe\x00", b"[]"])
def test_non_json_is_refused(raw: bytes) -> None:
    with pytest.raises(TriageFileError):
        parse_triage(raw, str(TASK_ID))


def test_another_tasks_triage_is_refused() -> None:
    with pytest.raises(TriageFileError, match="another task"):
        parse_triage(_raw(triage_file(task_id="someone-else")), str(TASK_ID))


def test_oversize_triage_is_refused(tmp_path: Path) -> None:
    raw = _raw(triage_file(padding="x" * MAX_TRIAGE_BYTES))
    with pytest.raises(TriageFileError, match="too large"):
        parse_triage(raw, str(TASK_ID))
    path = tmp_path / "triage.json"
    path.write_bytes(raw)
    with pytest.raises(TriageFileError, match="too large"):
        read_triage(path, str(TASK_ID))


def test_a_missing_triage_is_refused(tmp_path: Path) -> None:
    with pytest.raises(TriageFileError, match="no triage"):
        read_triage(tmp_path / "triage.json", str(TASK_ID))


def _always(_self: Path) -> bool:
    return True


def test_a_symlinked_triage_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "triage.json"
    path.write_bytes(_raw(triage_file()))
    monkeypatch.setattr(Path, "is_symlink", _always)
    with pytest.raises(TriageFileError, match="regular file"):
        read_triage(path, str(TASK_ID))
