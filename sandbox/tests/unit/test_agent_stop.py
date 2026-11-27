"""Get-task-first start, early stops and the run logs (no SDK, no network beyond loopback)."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

import agent_policy as ap
import agent_runner as ar
import coordinator_client as cc
import handoff
import llm_usage
import pytest
import run_log

ENV = {
    "TASK_ID": "T-1",
    "RUN_ID": "R-1",
    "COORDINATOR_URL": "http://coordinator:8100",
    "COORDINATOR_TOKEN": "jrc1.fake.fake",
    "ANTHROPIC_BASE_URL": "http://llm-proxy:8080",
    "ANTHROPIC_AUTH_TOKEN": "jrt2.fake.fake",
}
TWO_USD = 2_000_000


@dataclass
class FakeTransport:
    responses: dict[str, tuple[int, object]] = field(default_factory=dict[str, tuple[int, object]])
    calls: list[tuple[str, str, object]] = field(default_factory=list[tuple[str, str, object]])
    down: bool = False

    def __call__(
        self, method: str, url: str, headers: dict[str, str], body: bytes | None, timeout: float
    ) -> tuple[int, dict[str, str], bytes]:
        if self.down:
            raise cc.CoordinatorError("coordinator unreachable: ConnectionRefusedError")
        path = url.split("8100", 1)[1].split("?", 1)[0]
        self.calls.append((method, path, json.loads(body) if body else None))
        status, payload = self.responses.get(path, (200, {"ok": True}))
        return status, {"content-type": "application/json"}, json.dumps(payload).encode()


def _client(transport: FakeTransport) -> cc.CoordinatorClient:
    return cc.CoordinatorClient("http://coordinator:8100", "jrc1.fake.fake", transport=transport)


def _settings(tmp_path: Path, **env: str) -> ar.Settings:
    argv = ["--base-dir", str(tmp_path / "base"), "--out", str(tmp_path / "agent" / "result.json")]
    return ar.load_settings({**ENV, **env}, [*argv, "--log-dir", str(tmp_path / "out")])


def _lines(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


TASK = {"task_id": "T-1", "brief": "Add a badge.", "next_cursor": None}


# ------------------------------------------------------------------ start: get_task first


def test_begin_fetches_the_task_then_posts_started(tmp_path: Path) -> None:
    transport = FakeTransport({"/v1/task": (200, TASK)})
    tracker, log = ap.RunTracker(token_budget=1000), run_log.RunLog(tmp_path / "out")
    body = {"usd_micro_used": 0, "usd_micro_budget": TWO_USD, "requests": 0, "budget_refusals": 0}
    spend = llm_usage.UsageClient(
        "http://llm-proxy:8080", "jrt2.fake.fake", getter=_getter(200, body)
    )
    user_turn = ar.begin(_settings(tmp_path), _client(transport), tracker, log, spend)
    assert "Add a badge." in user_turn
    assert [(m, p) for m, p, _ in transport.calls] == [("GET", "/v1/task"), ("POST", "/v1/steps")]
    step = cc.as_object(transport.calls[1][2])
    assert step is not None and step["kind"] == "message"
    assert str(step["text"]).startswith("Started: model claude-opus")
    assert "$2.00" in str(step["text"])
    assert tracker.steps_posted == 1
    assert _lines(tmp_path / "out" / "steps.jsonl")[0]["text"] == step["text"]


@pytest.mark.parametrize(
    ("transport", "status"),
    [
        (FakeTransport({"/v1/task": (401, {"title": "Unauthorized"})}), "401"),
        (FakeTransport({"/v1/task": (403, {"title": "Forbidden"})}), "403"),
        (FakeTransport(down=True), "ConnectionRefusedError"),
        (FakeTransport({"/v1/task": (200, TASK), "/v1/steps": (403, {})}), "403"),
    ],
)
def test_begin_fails_fast_when_the_coordinator_is_unusable(
    tmp_path: Path, transport: FakeTransport, status: str
) -> None:
    tracker, log = ap.RunTracker(token_budget=1000), run_log.RunLog(tmp_path / "out")
    with pytest.raises(ar.CoordinatorUnreachableError) as err:
        ar.begin(_settings(tmp_path), _client(transport), tracker, log, _NO_PROXY)
    assert err.value.reason == f"coordinator unreachable: {status}"
    assert "jrc1" not in err.value.reason


_NO_PROXY = llm_usage.UsageClient("", "")


def _base(tmp_path: Path) -> None:
    (tmp_path / "base" / "docs" / "standards").mkdir(parents=True)
    (tmp_path / "base" / "AGENTS.md").write_text("# AGENTS\n")
    (tmp_path / "base" / "docs" / "standards" / "testing.md").write_text("# testing\n")


def test_main_exits_nonzero_without_touching_the_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _base(tmp_path)
    out = tmp_path / "agent" / "result.json"
    env = {
        **ENV,
        "COORDINATOR_URL": "http://127.0.0.1:9",
        "ANTHROPIC_BASE_URL": "http://127.0.0.1:9",
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    # Importing the SDK glue would mean the loop was about to start: make that an error.
    monkeypatch.setitem(sys.modules, "agent_sdk", None)
    argv = ["--base-dir", str(tmp_path / "base"), "--out", str(out), "--log-dir", str(tmp_path)]
    assert ar.main(argv) == ar.EXIT_COORDINATOR
    data = json.loads(out.read_text())
    assert data["stop"] == "coordinator_unreachable" and data["turns"] == 0
    assert data["detail"].startswith("Stopped: coordinator unreachable: ")
    assert "model was not called" in data["detail"]
    assert [e["event"] for e in _lines(tmp_path / "agent.log")] == ["stop"]
    assert not (tmp_path / "steps.jsonl").exists()
    # handoff.py turns it into a readable failed handoff, tests skipped (no changes).
    agent = handoff.read_agent_result(out)
    h = handoff.build(
        "T-1", handoff.Counts(), [], ["no changes; tests not run"], agent=agent, tests_ran=False
    )
    assert h["status"] == "failed"
    assert str(h["summary"]).startswith("Stopped: coordinator unreachable: ")
    assert "No changes; tests not run." in str(h["summary"])
    assert h["skipped_checks"] == ["no changes; tests not run"]


# ------------------------------------------------------------------ early stops


def _finish(
    tmp_path: Path,
    outcome: ar.LoopOutcome,
    spend: llm_usage.Spend | None,
    transport: FakeTransport | None = None,
    agent_handoff: dict[str, object] | None = None,
) -> tuple[dict[str, object], FakeTransport, handoff.AgentOutcome]:
    transport = transport or FakeTransport()
    settings = _settings(tmp_path, AGENT_TIMEOUT_S="1800", AGENT_MAX_TURNS="40")
    tracker = ap.RunTracker(token_budget=3_000_000)
    tracker.handoff = agent_handoff
    log = run_log.RunLog(tmp_path / "out")
    result = ar.finish(settings, tracker, outcome, spend, log, _client(transport))
    return result, transport, handoff.read_agent_result(settings.out)


SPENT = llm_usage.Spend(1_970_000, TWO_USD, 4, 1)


@pytest.mark.parametrize(
    ("outcome", "spend", "detail"),
    [
        # The CLI ends with an API error after llm-proxy's 403; the refusal count says why.
        (
            ar.LoopOutcome("error", 4, 403, "API Error: 403 task token budget exhausted"),
            SPENT,
            "Stopped: model budget used up after 4 turns ($1.97 of $2.00).",
        ),
        (ar.LoopOutcome("budget", 4), SPENT, "Stopped: model budget used up after 4 turns"),
        (
            ar.LoopOutcome("max_turns", 40),
            llm_usage.Spend(800_000, TWO_USD, 40, 0),
            "Stopped: turn limit of 40 reached after 40 turns ($0.80 of $2.00).",
        ),
        (
            ar.LoopOutcome("timeout", 0),
            llm_usage.Spend(100_000, TWO_USD, 3, 0),
            "Stopped: wall-clock limit of 1800s reached after",
        ),
        (
            ar.LoopOutcome("error", error="ProcessError"),
            None,
            "Stopped: the agent loop failed (ProcessError) after 0 turns (spend unknown).",
        ),
        (
            ar.LoopOutcome("end_turn", 6),
            llm_usage.Spend(300_000, TWO_USD, 6, 0),
            "Stopped: the agent ended without post_handoff after 6 turns ($0.30 of $2.00).",
        ),
    ],
)
def test_each_early_stop_becomes_a_readable_failed_handoff(
    tmp_path: Path, outcome: ar.LoopOutcome, spend: llm_usage.Spend | None, detail: str
) -> None:
    result, transport, agent = _finish(tmp_path, outcome, spend)
    assert str(result["detail"]).startswith(detail)
    # The runner posted a failed handoff itself, saying why in plain words.
    posts = [cc.as_object(b) for _, p, b in transport.calls if p == "/v1/handoff"]
    assert len(posts) == 1 and posts[0] is not None
    posted = cc.as_object(posts[0]["handoff"])
    assert posted is not None and posted["status"] == "failed"
    assert str(posted["summary"]).startswith(detail)
    assert result["runner_posted_handoff"] is True
    # ... and the sandbox handoff leads with the same sentence.
    h = handoff.build("T-1", handoff.Counts(passed=2), ["web/a.ts"], [], agent=agent)
    assert h["status"] == "failed"
    assert str(h["summary"]).startswith(detail)
    assert "Sandbox re-run: 2 passed" in str(h["summary"])
    assert len(str(h["summary"]).split()) <= handoff.MAX_SUMMARY_WORDS


def test_budget_stop_is_recognised_from_the_api_error_alone(tmp_path: Path) -> None:
    outcome = ar.LoopOutcome("error", 4, 403, "API Error: 403 task token budget exhausted")
    result, _, _ = _finish(tmp_path, outcome, None)
    assert result["stop"] == "budget"
    assert result["detail"] == "Stopped: model budget used up after 4 turns (spend unknown)."


def test_other_api_errors_stay_errors(tmp_path: Path) -> None:
    result, _, _ = _finish(tmp_path, ar.LoopOutcome("error", 2, 529, "Overloaded"), None)
    assert result["stop"] == "error"


def test_no_runner_handoff_when_the_agent_posted_one(tmp_path: Path) -> None:
    posted: dict[str, object] = {"status": "done", "summary": "Done."}
    result, transport, agent = _finish(
        tmp_path, ar.LoopOutcome("end_turn", 5), llm_usage.Spend(1, TWO_USD, 5, 0), None, posted
    )
    assert [p for _, p, _ in transport.calls] == []
    assert result["runner_posted_handoff"] is False
    assert agent.finished


def test_runner_handoff_failure_still_leaves_the_result(tmp_path: Path) -> None:
    result, _, agent = _finish(
        tmp_path, ar.LoopOutcome("budget", 3), SPENT, FakeTransport(down=True)
    )
    assert result["runner_posted_handoff"] is False
    assert agent.stop == "budget" and agent.detail is not None
    assert agent.detail.startswith("Stopped: model budget used up after 3 turns")


def test_killed_runner_still_gets_a_readable_summary(tmp_path: Path) -> None:
    agent = handoff.read_agent_result(tmp_path / "absent.json")
    h = handoff.build("T-1", handoff.Counts(), [], [], agent=agent, tests_ran=False)
    assert str(h["summary"]).startswith("Stopped: wall-clock limit reached")


# ------------------------------------------------------------------ logs


def test_agent_log_has_one_metadata_line_per_turn(tmp_path: Path) -> None:
    log = run_log.RunLog(tmp_path)
    buf = ar.TurnBuffer()
    usage = {"input_tokens": 3, "output_tokens": 50, "cache_read_input_tokens": 20_000}
    # One API response split over two messages with the same id, then a second response.
    assert buf.add("msg_1", [], usage) is None
    assert buf.add("msg_1", ["Read"], usage) is None
    first = buf.add("msg_2", ["mcp__coordinator__post_step", "Edit"], {"output_tokens": 9})
    assert first is not None
    log.turn(first[0], first[1], 12_000, TWO_USD)
    second = buf.take()
    assert second is not None and buf.take() is None
    log.turn(second[0], second[1], 20_000, TWO_USD)
    lines = _lines(tmp_path / "agent.log")
    assert [(e["turn"], e["tools"]) for e in lines] == [
        (1, ["Read"]),
        (2, ["mcp__coordinator__post_step", "Edit"]),
    ]
    assert lines[0]["tokens"] == {
        "input_tokens": 3,
        "output_tokens": 50,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 20_000,
    }
    assert lines[1]["usd_micro_used"] == 20_000 and lines[1]["usd_micro_budget"] == TWO_USD
    assert set(lines[0]) == {
        "event",
        "turn",
        "ts",
        "tools",
        "tokens",
        "usd_micro_used",
        "usd_micro_budget",
    }


# ------------------------------------------------------------------ llm-proxy usage client


def _getter(status: int, body: object) -> llm_usage.Getter:
    def get(url: str, headers: dict[str, str], timeout: float) -> tuple[int, bytes]:
        assert url == "http://llm-proxy:8080/v1/usage"
        assert headers == {"authorization": "Bearer jrt2.fake.fake"}
        return status, json.dumps(body).encode()

    return get


def test_usage_client_reads_the_spend() -> None:
    body = {"usd_micro_used": 5, "usd_micro_budget": 9, "requests": 2, "budget_refusals": 0}
    client = llm_usage.UsageClient(
        "http://llm-proxy:8080/", "jrt2.fake.fake", getter=_getter(200, body)
    )
    spend = client.fetch()
    assert spend is not None
    assert spend == llm_usage.Spend(5, 9, 2, 0) and not spend.exhausted
    assert spend.as_dict() == body
    assert llm_usage.Spend(9, 9, 2, 0).exhausted and llm_usage.Spend(1, 9, 2, 1).exhausted


@pytest.mark.parametrize(
    ("status", "body"),
    [(401, {}), (200, []), (200, {"usd_micro_used": "5"}), (200, {"usd_micro_used": -1})],
)
def test_usage_client_returns_none_rather_than_guessing(status: int, body: object) -> None:
    getter = _getter(status, body)
    assert (
        llm_usage.UsageClient("http://llm-proxy:8080", "jrt2.fake.fake", getter=getter).fetch()
        is None
    )


def test_usage_client_survives_a_dead_proxy() -> None:
    def down(url: str, headers: dict[str, str], timeout: float) -> tuple[int, bytes]:
        raise ConnectionRefusedError

    assert llm_usage.UsageClient("http://llm-proxy:8080", "t", getter=down).fetch() is None
    assert llm_usage.UsageClient("", "t").fetch() is None
