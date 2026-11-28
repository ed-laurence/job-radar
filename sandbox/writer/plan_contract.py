"""The ``/out/plan.json`` contract of a plan run (stdlib only).

The backend parses this file strictly (unknown keys rejected), so the planner's ``submit_plan``
tool validates the model's plan here before anything is written, and returns every problem as a
tool error the model can fix. ``validate`` also fills the optional fields so the file always
carries every key::

    {"task_id", "mode": "single"|"multi",
     "subtasks": [{"id" <=64, "title" <=200, "agent" <=64, "lease_paths" [<=300] (1..50),
                   "depends_on" [subtask ids] (<=20), "test_only": bool}] (1..20),
     "acceptance_criteria": [{"test_id": "<test file>::<test name>" <=300,
                              "expectation" <=1000}] (1..30),
     "tests_to_change": [existing repo-relative test file] (<=50),
     "budget_usd": 0..20, "budget_minutes": 1..120, "risk": "low"|"medium"|"high"}

The planner writes the acceptance criteria (triage doesn't); the plan's acceptance tests are
their test ids, once each (``acceptance_tests``). Every criterion names a test file
(``backend/tests/``, ``web/tests/`` or ``*.test.ts(x)``), and web unit tests under ``web/src/``
must be ``*.test.ts(x)`` files. With ``repo`` (the read-only checkout) paths are also checked
against the real tree: a lease or a new criterion test file must sit in a directory that
exists, and ``tests_to_change`` must name existing test files. A
plan that leases ``api/openapi.yaml`` must also lease both generated dirs and give its test
subtask(s) ``backend/tests/contract/`` and ``backend/tests/integration/``.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast

import agent_policy

TOP_KEYS = (
    "task_id",
    "mode",
    "subtasks",
    "acceptance_criteria",
    "tests_to_change",
    "budget_usd",
    "budget_minutes",
    "risk",
)
SUBTASK_KEYS = ("id", "title", "agent", "lease_paths", "depends_on", "test_only")
CRITERION_KEYS = ("test_id", "expectation")
MODES = ("single", "multi")
RISKS = ("low", "medium", "high")
MAX_SUBTASKS = 20
MAX_ID, MAX_TITLE, MAX_AGENT, MAX_PATH = 64, 200, 64, 300
MAX_LEASES, MAX_DEPENDS = 50, 20
MAX_CRITERIA, MAX_TEST_CHARS, MAX_EXPECTATION = 30, 300, 1000
MAX_TESTS_TO_CHANGE = 50
MAX_BUDGET_USD, MAX_MINUTES = 20, 120
MAX_ERRORS = 15
_SHOW = 80  # characters of a value quoted back in an error

API_SPEC = "api/openapi.yaml"
GENERATED_DIRS = ("backend/src/app/generated", "web/src/lib/api/generated")
API_TEST_DIRS = ("backend/tests/contract", "backend/tests/integration")


class PlanError(Exception):
    """The plan breaks the contract; ``errors`` are short, fixable messages."""

    def __init__(self, errors: Sequence[str]) -> None:
        self.errors = list(errors)[:MAX_ERRORS]
        super().__init__("plan refused: " + "; ".join(self.errors))


def as_dicts(value: object) -> list[dict[str, object]]:
    items = cast("list[object]", value) if isinstance(value, list) else []
    return [cast("dict[str, object]", v) for v in items if isinstance(v, dict)]


def show(value: str) -> str:
    return value if len(value) <= _SHOW else value[:_SHOW] + "..."


class Checker:
    def __init__(self, repo: Path | None) -> None:
        self.errors: list[str] = []
        self.repo = repo

    def err(self, message: str) -> None:
        self.errors.append(message)

    # ------------------------------------------------------------ primitives
    def text(self, where: str, value: object, limit: int) -> str:
        if not isinstance(value, str) or not value.strip():
            self.err(f"{where} must be a non-empty string")
            return ""
        if len(value) > limit:
            self.err(f"{where} is longer than {limit} characters")
            return ""
        return value

    def strings(self, where: str, value: object, lo: int, hi: int, limit: int) -> list[str]:
        if not isinstance(value, list):
            self.err(f"{where} must be a list of strings")
            return []
        items = cast("list[object]", value)
        if not lo <= len(items) <= hi:
            self.err(f"{where} must have {lo}-{hi} items (has {len(items)})")
            return []
        out = [self.text(f"{where}[{i}]", item, limit) for i, item in enumerate(items)]
        return [s for s in out if s]

    def keys(self, where: str, obj: Mapping[str, object], allowed: Sequence[str]) -> None:
        unknown = sorted(set(obj) - set(allowed))
        if unknown:
            self.err(f"{where}: unknown key(s) {', '.join(show(k) for k in unknown[:5])}")

    def rel_path(self, where: str, path: str, *, globs_ok: bool = False) -> str | None:
        if path.startswith("/") or "\\" in path or ".." in path.split("/") or "\x00" in path:
            self.err(f"{where}: {show(path)} must be repo-relative (no '/', '\\' or '..')")
            return None
        if not globs_ok and ("*" in path or "?" in path):
            self.err(f"{where}: {show(path)} is a glob; use a file path or a 'dir/' prefix")
            return None
        return agent_policy.normalise_entry(path)

    # ------------------------------------------------------------ tree checks
    def dir_exists(self, rel: str) -> bool:
        return self.repo is None or rel == "" or (self.repo / rel).is_dir()

    def file_exists(self, rel: str) -> bool:
        return self.repo is None or (self.repo / rel).is_file()

    def parent_exists(self, where: str, rel: str, what: str) -> None:
        if self.repo is None or (self.repo / rel).exists():
            return
        parent = rel.rsplit("/", 1)[0] if "/" in rel else ""
        if not self.dir_exists(parent):
            self.err(
                f"{where}: {show(rel)}: {what} directory {show(parent)}/ does not exist in "
                "the repo; use an existing directory (check with Glob)"
            )


def _subtasks(c: Checker, value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        c.err("subtasks must be a list of objects")
        return []
    items = cast("list[object]", value)
    if not 1 <= len(items) <= MAX_SUBTASKS:
        c.err(f"subtasks must have 1-{MAX_SUBTASKS} items (has {len(items)})")
        return []
    out: list[dict[str, object]] = []
    for i, item in enumerate(items):
        where = f"subtasks[{i}]"
        if not isinstance(item, dict):
            c.err(f"{where} must be an object")
            continue
        raw = cast("dict[str, object]", item)
        c.keys(where, raw, SUBTASK_KEYS)
        leases = c.strings(f"{where}.lease_paths", raw.get("lease_paths"), 1, MAX_LEASES, MAX_PATH)
        for j, path in enumerate(leases):
            rel = c.rel_path(f"{where}.lease_paths[{j}]", path)
            if rel is not None:
                c.parent_exists(f"{where}.lease_paths[{j}]", rel, "lease")
        depends = raw.get("depends_on", [])
        test_only = raw.get("test_only", False)
        if not isinstance(test_only, bool):
            c.err(f"{where}.test_only must be true or false")
        out.append(
            {
                "id": c.text(f"{where}.id", raw.get("id"), MAX_ID),
                "title": c.text(f"{where}.title", raw.get("title"), MAX_TITLE),
                "agent": c.text(f"{where}.agent", raw.get("agent"), MAX_AGENT),
                "lease_paths": leases,
                "depends_on": c.strings(f"{where}.depends_on", depends, 0, MAX_DEPENDS, MAX_ID),
                "test_only": test_only is True,
            }
        )
    ids = [str(s["id"]) for s in out]
    for sid in sorted({i for i in ids if i and ids.count(i) > 1}):
        c.err(f"duplicate subtask id {show(sid)}")
    for sub in out:
        for dep in cast("list[str]", sub["depends_on"]):
            if dep == sub["id"] or dep not in ids:
                c.err(f"subtask {show(str(sub['id']))}: depends_on {show(dep)} is not another id")
    return out


def _test_id(c: Checker, where: str, test: str) -> None:
    path, sep, name = test.partition("::")
    if not sep or not path.strip() or not name.strip():
        c.err(f"{where}: {show(test)} must be '<test file>::<test name>'")
        return
    rel = c.rel_path(where, path.strip())
    if rel is None:
        return
    if not agent_policy.is_test_path(rel):
        c.err(
            f"{where}: {show(rel)} is not a test file (backend/tests/, web/tests/ or *.test.ts(x))"
        )
    elif rel.startswith("web/src/") and not rel.endswith((".test.ts", ".test.tsx")):
        c.err(f"{where}: web unit tests sit beside the component as *.test.ts or *.test.tsx")
    elif c.repo is not None and not c.file_exists(rel):
        c.parent_exists(where, rel, "test")


def _criteria(c: Checker, value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        c.err("acceptance_criteria must be a list of objects")
        return []
    items = cast("list[object]", value)
    if not 1 <= len(items) <= MAX_CRITERIA:
        c.err(f"acceptance_criteria must have 1-{MAX_CRITERIA} items (has {len(items)})")
        return []
    out: list[dict[str, object]] = []
    for i, item in enumerate(items):
        where = f"acceptance_criteria[{i}]"
        if not isinstance(item, dict):
            c.err(f"{where} must be an object {{test_id, expectation}}")
            continue
        raw = cast("dict[str, object]", item)
        c.keys(where, raw, CRITERION_KEYS)
        test = c.text(f"{where}.test_id", raw.get("test_id"), MAX_TEST_CHARS)
        if test:
            _test_id(c, f"{where}.test_id", test)
        expectation = c.text(f"{where}.expectation", raw.get("expectation"), MAX_EXPECTATION)
        out.append({"test_id": test, "expectation": expectation})
    return out


def acceptance_tests(plan: Mapping[str, object]) -> list[str]:
    """The plan's acceptance tests: its criteria's test ids, once each, in order."""
    tests = (str(c["test_id"]) for c in as_dicts(plan.get("acceptance_criteria")))
    return list(dict.fromkeys(tests))


def _tests_to_change(c: Checker, value: object) -> list[str]:
    paths = c.strings("tests_to_change", value, 0, MAX_TESTS_TO_CHANGE, MAX_PATH)
    for i, path in enumerate(paths):
        where = f"tests_to_change[{i}]"
        rel = c.rel_path(where, path)
        if rel is None:
            continue
        if not agent_policy.is_test_path(rel):
            c.err(f"{where}: {show(rel)} is not a test file")
        elif not c.file_exists(rel):
            c.err(f"{where}: {show(rel)} does not exist; list only EXISTING test files")
    return paths


def _api_rules(c: Checker, subtasks: list[dict[str, object]]) -> None:
    def leases(subs: list[dict[str, object]]) -> list[str]:
        return [p for s in subs for p in cast("list[str]", s["lease_paths"])]

    every = leases(subtasks)
    if not agent_policy.covers(every, API_SPEC):
        return
    for target in GENERATED_DIRS:
        if not agent_policy.covers(every, target):
            c.err(f"the API changes: lease {target}/ (scripts/gen-api.sh rewrites it)")
    testers = [s for s in subtasks if s["test_only"]] or subtasks
    for target in API_TEST_DIRS:
        if not agent_policy.covers(leases(testers), target):
            c.err(f"the API changes: the test subtask must lease {target}/")


def _in_budget(value: object) -> bool:
    if not isinstance(value, int | float) or isinstance(value, bool):
        return False
    return math.isfinite(value) and 0 <= value <= MAX_BUDGET_USD


def validate(raw: object, task_id: str, repo: Path | None = None) -> dict[str, object]:
    """The plan in contract shape (optional fields filled), or ``PlanError``."""
    c = Checker(repo)
    if not isinstance(raw, dict):
        raise PlanError(["the plan must be a JSON object"])
    data = cast("dict[str, object]", raw)
    c.keys("plan", data, TOP_KEYS)
    given = data.get("task_id", task_id)
    if given != task_id:
        c.err(f"task_id must be {task_id}")
    mode = data.get("mode")
    if mode not in MODES:
        c.err("mode must be 'single' or 'multi'")
    subtasks = _subtasks(c, data.get("subtasks"))
    criteria = _criteria(c, data.get("acceptance_criteria"))
    to_change = _tests_to_change(c, data.get("tests_to_change", []))
    budget = data.get("budget_usd")
    if not _in_budget(budget):
        c.err(f"budget_usd must be a number 0-{MAX_BUDGET_USD}")
    minutes = data.get("budget_minutes")
    if not isinstance(minutes, int) or isinstance(minutes, bool) or not 1 <= minutes <= MAX_MINUTES:
        c.err(f"budget_minutes must be an integer 1-{MAX_MINUTES}")
    if data.get("risk") not in RISKS:
        c.err("risk must be 'low', 'medium' or 'high'")
    if subtasks:
        _api_rules(c, subtasks)
    if c.errors:
        raise PlanError(c.errors)
    return {
        "task_id": task_id,
        "mode": mode,
        "subtasks": subtasks,
        "acceptance_criteria": criteria,
        "tests_to_change": to_change,
        "budget_usd": budget,
        "budget_minutes": minutes,
        "risk": data.get("risk"),
    }


def write(plan: Mapping[str, object], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(plan, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)
