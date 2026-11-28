"""The agent's coordinator tools over a fake transport (no network, no SDK)."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import agent_policy as ap
import coordinator_client as cc
import coordinator_tools as ct
import pytest

TOKEN = "jrc1.fake.fake"


@dataclass
class FakeTransport:
    responses: dict[str, tuple[int, object]] = field(default_factory=dict[str, tuple[int, object]])
    calls: list[tuple[str, str, object]] = field(default_factory=list[tuple[str, str, object]])

    def __call__(
        self, method: str, url: str, headers: dict[str, str], body: bytes | None, timeout: float
    ) -> tuple[int, dict[str, str], bytes]:
        path = url.split("8100", 1)[1].split("?", 1)[0]
        self.calls.append((method, path, json.loads(body) if body else None))
        status, payload = self.responses.get(path, (200, {"ok": True}))
        return status, {"content-type": "application/json"}, json.dumps(payload).encode()


def _tools() -> tuple[ct.CoordinatorTools, ap.RunTracker, FakeTransport]:
    transport = FakeTransport()
    client = cc.CoordinatorClient("http://coordinator:8100", TOKEN, transport=transport)
    tracker = ap.RunTracker(token_budget=100)
    return ct.CoordinatorTools(client, tracker), tracker, transport


def _call(tools: ct.CoordinatorTools, name: str, args: dict[str, object]) -> dict[str, object]:
    return asyncio.run(tools.call(name, args))


def _text(result: dict[str, object]) -> str:
    content = cc.as_list(result["content"])
    first = cc.as_object(content[0]) or {}
    return str(first["text"])


def test_specs_cover_the_coordinator_tools_with_strict_schemas() -> None:
    assert ct.NAMES == ap.COORDINATOR_TOOLS
    for spec in ct.SPECS:
        assert spec.input_schema["additionalProperties"] is False


def test_claim_tracks_only_granted_paths() -> None:
    tools, tracker, transport = _tools()
    transport.responses["/v1/leases/claim"] = (
        200,
        {"granted": [{"path": "web/src/a.ts"}], "timed_out": False},
    )
    result = _call(tools, "claim_paths", {"paths": ["web/src/a.ts", "web/src/b.ts"], "wait_s": 5})
    assert result["is_error"] is False
    assert tracker.leases == ["web/src/a.ts"]
    assert transport.calls[-1] == (
        "POST",
        "/v1/leases/claim",
        {"paths": ["web/src/a.ts", "web/src/b.ts"], "wait_s": 5},
    )
    transport.responses["/v1/leases/claim"] = (200, {"granted": [], "timed_out": True})
    _call(tools, "claim_paths", {"paths": ["api/openapi.yaml"]})
    assert tracker.leases == ["web/src/a.ts"]


@pytest.mark.parametrize(
    "args",
    [
        {"paths": []},
        {"paths": ["web/**/*.ts"]},
        {"paths": ["/etc/passwd"]},
        {"paths": ["../x"]},
        {"paths": ["a"], "wait_s": 61},
        {"paths": ["a"], "wait_s": True},
        {"paths": ["ok", 3]},
    ],
)
def test_bad_claims_refused_locally(args: dict[str, object]) -> None:
    tools, tracker, transport = _tools()
    result = _call(tools, "claim_paths", args)
    assert result["is_error"] is True and "refused" in _text(result)
    assert transport.calls == [] and tracker.leases == []


def test_release_steps_handoff_help() -> None:
    tools, tracker, transport = _tools()
    tracker.add_leases(["web/a.ts", "web/b/"])
    _call(tools, "release_paths", {"paths": ["web/a.ts"]})
    assert tracker.leases == ["web/b/"]
    _call(tools, "release_paths", {})
    assert tracker.leases == []
    assert (
        _call(tools, "post_step", {"kind": "message", "text": "tests written"})["is_error"] is False
    )
    assert tracker.steps_posted == 1
    handoff: dict[str, object] = {"status": "done", "summary": "Added the badge."}
    _call(tools, "post_handoff", {"handoff": handoff})
    assert tracker.handoff == handoff
    _call(tools, "request_help", {"text": "lease wait timed out"})
    assert tracker.help_requested
    assert [c[1] for c in transport.calls] == [
        "/v1/leases/release",
        "/v1/leases/release",
        "/v1/steps",
        "/v1/handoff",
        "/v1/help",
    ]


def test_oversized_and_invalid_steps_refused_not_cut() -> None:
    tools, tracker, transport = _tools()
    long = _call(tools, "post_step", {"kind": "message", "text": "x" * (cc.MAX_STEP_CHARS + 1)})
    bad_kind = _call(tools, "post_step", {"kind": "handoff", "text": "x"})
    assert long["is_error"] is True and bad_kind["is_error"] is True
    assert transport.calls == [] and tracker.steps_posted == 0


def test_server_refusals_are_tool_errors_and_not_recorded() -> None:
    tools, tracker, transport = _tools()
    transport.responses["/v1/handoff"] = (413, {"title": "Payload Too Large"})
    result = _call(tools, "post_handoff", {"handoff": {"status": "done", "summary": "s"}})
    assert result["is_error"] is True and "413" in _text(result)
    assert tracker.handoff is None
    assert TOKEN not in _text(result)


def test_post_handoff_schema_matches_the_coordinator() -> None:
    # The tool's schema spells out what the coordinator accepts, so the model gets it right.
    spec = next(spec for spec in ct.SPECS if spec.name == "post_handoff")
    handoff = _obj(_obj(spec.input_schema["properties"])["handoff"])
    props = _obj(handoff["properties"])
    assert handoff["additionalProperties"] is False
    assert _obj(props["status"])["enum"] == ["done", "partial", "blocked", "failed"]
    tests = _obj(props["tests"])
    assert tests["additionalProperties"] is False
    assert set(_obj(tests["properties"])) == {"passed", "failed", "first_failing"}
    assert tests["required"] == ["passed", "failed"]
    assert handoff["required"] == ["status", "summary", "tests"]


def _obj(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return cast("dict[str, object]", value)


def test_get_task_and_unknown_tool() -> None:
    tools, _, transport = _tools()
    transport.responses["/v1/task"] = (200, {"brief": "b", "next_cursor": None})
    assert json.loads(_text(_call(tools, "get_task", {})))["brief"] == "b"
    assert _call(tools, "drop_tables", {})["is_error"] is True


def test_oversized_results_refused() -> None:
    result = ct.text_result({"brief": "x" * (ct.RESULT_CHARS + 1)})
    assert result["is_error"] is True and "too large" in _text(result)


# ------------------------------------------------------------------ plan mode


def _plan_tools(
    tmp_path: Path,
) -> tuple[ct.CoordinatorTools, ap.RunTracker, FakeTransport, Path]:
    repo = tmp_path / "repo"
    (repo / "web/src/features/board").mkdir(parents=True)
    (repo / "web/src/features/board/Card.test.tsx").write_text("x\n")
    out = tmp_path / "out" / "plan.json"
    transport = FakeTransport()
    client = cc.CoordinatorClient("http://coordinator:8100", TOKEN, transport=transport)
    tracker = ap.RunTracker(token_budget=100, plan_mode=True)
    sink = ct.PlanSink(task_id="T-1", repo=repo, out=out)
    return ct.CoordinatorTools(client, tracker, plan_sink=sink), tracker, transport, out


PLAN: dict[str, object] = {
    "mode": "single",
    "subtasks": [
        {
            "id": "s1",
            "title": "Card badge",
            "agent": "frontend-engineer",
            "lease_paths": ["web/src/features/board/"],
        }
    ],
    "acceptance_criteria": [
        {
            "test_id": "web/src/features/board/Card.test.tsx::shows the badge",
            "expectation": "The card shows a priority badge.",
        }
    ],
    "tests_to_change": ["web/src/features/board/Card.test.tsx"],
    "budget_usd": 2,
    "budget_minutes": 20,
    "risk": "low",
}


def test_plan_specs_are_read_only_plus_submit_plan(tmp_path: Path) -> None:
    tools, _, transport, _ = _plan_tools(tmp_path)
    assert tuple(s.name for s in tools.specs) == ap.PLAN_COORDINATOR_TOOLS
    assert tuple(s.name for s in ct.PLAN_SPECS) == ap.PLAN_COORDINATOR_TOOLS
    for spec in tools.specs:
        assert spec.input_schema["additionalProperties"] is False
    for name in ("claim_paths", "renew_lease", "release_paths", "post_handoff"):
        assert _call(tools, name, {"paths": ["web/"]})["is_error"] is True
    assert transport.calls == []
    dev, _, _ = _tools()
    assert "submit_plan" not in [s.name for s in dev.specs]
    assert _call(dev, "submit_plan", {"plan": PLAN})["is_error"] is True


def test_submit_plan_writes_the_contract_file(tmp_path: Path) -> None:
    tools, tracker, transport, out = _plan_tools(tmp_path)
    result = _call(tools, "submit_plan", {"plan": PLAN})
    assert result["is_error"] is False, _text(result)
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["task_id"] == "T-1"
    assert written["subtasks"][0]["depends_on"] == []
    assert written["subtasks"][0]["test_only"] is False
    assert tracker.plan_submitted is True
    assert transport.calls == []  # the plan goes to /out, not the coordinator


def test_an_invalid_plan_is_a_fixable_tool_error_and_nothing_is_written(tmp_path: Path) -> None:
    tools, tracker, _, out = _plan_tools(tmp_path)
    criteria = [{"test_id": "web/src/nowhere/x/Card.test.tsx::a", "expectation": "It works."}]
    bad = {**PLAN, "acceptance_criteria": criteria, "colour": 1}
    result = _call(tools, "submit_plan", {"plan": bad})
    assert result["is_error"] is True
    text = _text(result)
    assert "web/src/nowhere/x" in text and "colour" in text and "submit_plan again" in text
    assert not out.exists() and tracker.plan_submitted is False
    assert _call(tools, "submit_plan", {"plan": "nope"})["is_error"] is True
    assert _call(tools, "submit_plan", {})["is_error"] is True
