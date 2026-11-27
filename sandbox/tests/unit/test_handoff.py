import json
from pathlib import Path

import handoff
import pytest
from llm_proxy.jsonval import as_list

PYTEST_REPORT = {
    "tests": [
        {"id": "tests/unit/test_a.py::test_ok", "outcome": "passed"},
        {"id": "tests/unit/test_a.py::test_bad", "outcome": "failed"},
        {"id": "tests/unit/test_b.py::test_skip", "outcome": "skipped"},
    ]
}
VITEST_REPORT = {
    "testResults": [
        {
            "name": "/workspace/repo/web/src/a.test.ts",
            "assertionResults": [
                {"fullName": "board renders", "status": "passed"},
                {"fullName": "board drags", "status": "failed"},
                {"fullName": "board later", "status": "todo"},
            ],
        }
    ]
}
KEYS = {
    "task_id",
    "status",
    "summary",
    "changed_paths",
    "tests",
    "rubric_checked",
    "open_questions",
    "skipped_checks",
}


def _write(tmp_path: Path, name: str, data: object) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(data))
    return path


def test_counts_and_first_failing_id(tmp_path: Path) -> None:
    counts = handoff.Counts()
    handoff.read_pytest(_write(tmp_path, "b.json", PYTEST_REPORT), counts)
    handoff.read_vitest(_write(tmp_path, "w.json", VITEST_REPORT), counts)
    assert (counts.passed, counts.failed, counts.skipped) == (2, 2, 2)
    assert counts.first_failing == "tests/unit/test_a.py::test_bad"


def test_handoff_shape_and_caps() -> None:
    out = handoff.build("T-1", handoff.Counts(), [], ["x" * 500] * 20, summary="w " * 200)
    assert set(out) == KEYS
    assert out["status"] == "done"
    assert len(str(out["summary"]).split()) == handoff.MAX_SUMMARY_WORDS
    skipped = [str(s) for s in as_list(out["skipped_checks"])]
    assert len(skipped) == handoff.MAX_SKIPPED
    assert all(len(s) <= handoff.MAX_ITEM_CHARS for s in skipped)


def test_failures_mark_status_failed() -> None:
    assert handoff.build("T-1", handoff.Counts(failed=1), [], [])["status"] == "failed"
    assert handoff.build("T-1", handoff.Counts(), [], [])["skipped_checks"] == ["none"]


def test_cli_writes_changed_paths(tmp_path: Path) -> None:
    report = _write(tmp_path, "b.json", PYTEST_REPORT)
    changed = tmp_path / "changed.txt"
    changed.write_text("web/b.ts\napi/openapi.yaml\n\nweb/b.ts\n")
    out = tmp_path / "handoff.json"
    argv = ["--task-id", "T-9", "--changed", str(changed), "--pytest-json", str(report)]
    assert handoff.main([*argv, "--out", str(out)]) == 0
    data = json.loads(out.read_text())
    assert set(data) == KEYS
    assert data["changed_paths"] == ["api/openapi.yaml", "web/b.ts"]
    assert data["status"] == "failed"
    assert data["tests"]["first_failing"] == "tests/unit/test_a.py::test_bad"


def test_pytest_report_plugin_records_outcomes_only(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    report = tmp_path / "report.json"
    monkeypatch.setenv("SANDBOX_PYTEST_REPORT", str(report))
    pytester.makepyfile(
        test_x="def test_ok():\n    print('OUTPUT-CANARY')\n"
        "def test_bad():\n    raise AssertionError('OUTPUT-CANARY')\n"
    )
    pytester.runpytest_inprocess("-p", "pytest_report", "-q")
    text = report.read_text()
    assert "OUTPUT-CANARY" not in text
    outcomes = {t["id"].split("::")[-1]: t["outcome"] for t in json.loads(text)["tests"]}
    assert outcomes == {"test_ok": "passed", "test_bad": "failed"}


def _agent_result(tmp_path: Path, **over: object) -> Path:
    data: dict[str, object] = {
        "stop": "end_turn",
        "turns": 7,
        "tokens_used": 1234,
        "posted_handoff": True,
        "handoff": {
            "status": "done",
            "summary": "Added the card badge with tests.",
            "rubric_checked": ["ARCH-01", "TEST-02", 7],
            "open_questions": ["q1", "q2", "q3", "q4"],
            "tests": {"passed": 999, "failed": 0},
            "changed_paths": ["claimed/by/agent.ts"],
        },
    }
    data.update(over)
    return _write(tmp_path, "agent.json", data)


def test_agent_handoff_merges_summary_but_not_claims(tmp_path: Path) -> None:
    report = _write(tmp_path, "b.json", PYTEST_REPORT)
    changed = tmp_path / "changed.txt"
    changed.write_text("web/src/features/board/card.tsx\n")
    out = tmp_path / "handoff.json"
    argv = ["--task-id", "T-2", "--changed", str(changed), "--pytest-json", str(report)]
    argv += ["--agent-mode", "--agent-result", str(_agent_result(tmp_path)), "--out", str(out)]
    assert handoff.main(argv) == 0
    data = json.loads(out.read_text())
    assert set(data) == KEYS
    assert data["summary"] == "Added the card badge with tests."
    assert data["rubric_checked"] == ["ARCH-01", "TEST-02"]
    assert data["open_questions"] == ["q1", "q2", "q3"]
    # Counts and paths come from the sandbox re-run and git, not the agent's claims.
    assert data["tests"]["passed"] == 1 and data["tests"]["failed"] == 1
    assert data["changed_paths"] == ["web/src/features/board/card.tsx"]
    assert data["status"] == "failed"


def test_agent_finished_and_green_is_done(tmp_path: Path) -> None:
    agent = handoff.read_agent_result(_agent_result(tmp_path))
    assert handoff.build("T-3", handoff.Counts(passed=3), [], [], agent=agent)["status"] == "done"


@pytest.mark.parametrize(
    ("over", "needle"),
    [
        ({"stop": "budget"}, "(budget)"),
        ({"stop": "max_turns"}, "(max_turns)"),
        ({"handoff": {}, "posted_handoff": False}, "without post_handoff"),
        ({"handoff": {"status": "blocked", "summary": "Lease wait."}}, "status blocked"),
    ],
)
def test_unfinished_agent_runs_fail(tmp_path: Path, over: dict[str, object], needle: str) -> None:
    agent = handoff.read_agent_result(_agent_result(tmp_path, **over))
    out = handoff.build("T-4", handoff.Counts(passed=1), [], [], agent=agent)
    assert out["status"] == "failed"
    assert needle in str(out["summary"])
    assert "1 passed" in str(out["summary"])


def test_missing_agent_result_means_timeout(tmp_path: Path) -> None:
    agent = handoff.read_agent_result(tmp_path / "absent.json")
    assert agent.stop == "timeout" and not agent.finished
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert handoff.read_agent_result(bad).stop == "error"
