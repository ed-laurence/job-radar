"""Tool policy for the writer agent (stdlib only; advisory, the container is the boundary).

Decides which tool calls the agent may make inside the sandbox:

- ``bash_allowed``: Bash only for an allowlist of test, lint and git commands, no shell
  metacharacters, and at most one ``cd <dir> &&`` prefix inside the workspace.
- ``file_tool_allowed``: Read/Glob/Grep only inside the workspace (so ``/proc/self/environ`` and
  the tokens in it stay out of reach); Edit/Write only on paths covered by a claimed lease and
  never on protected paths.
- Dev mode also refuses Edit/Write on an EXISTING test file (``is_test_path`` and tracked at
  the base commit) unless the card carries a ``modify-tests:`` label covering it, and allows
  Bash ``scripts/gen-api.sh`` only while a granted lease covers ``api/openapi.yaml``.
- Plan mode (``plan=True``, the read-only dev-lead planner): Read/Glob/Grep, Bash only for
  read-only git (``ls-files``, ``log``, ``grep``, ``show``, ``status``, ``diff``), no Edit/Write,
  and only the plan coordinator tools (no leases, no handoff).
- Triage mode (``triage=True``, the read-only triager): plan mode's tools, plus Bash for
  running EXISTING tests to reproduce an issue: ``cd backend && uv run pytest [opts] <test
  file>[::node]...`` and ``cd web && pnpm vitest run [-t <name>] <*.test.ts(x)>...``, where
  every file is a test tracked at the base commit (``existing_tests``). Options are an
  allowlist (no plugins, config overrides or output files); a whole-suite run is refused.
- ``RunTracker``: counts tokens the way llm-proxy does; the coordinator tools record granted
  leases, steps and the final ``post_handoff`` (or ``submit_plan``) in it.

The Publisher's checks are authoritative; this is fast feedback that keeps honest agents on track.
"""

from __future__ import annotations

import posixpath
import re
import shlex
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

WORKSPACE = "/workspace/repo"
MCP_PREFIX = "mcp__coordinator__"
FILE_READ_TOOLS = ("Read", "Glob", "Grep")
FILE_WRITE_TOOLS = ("Edit", "Write")
BASE_TOOLS = (*FILE_READ_TOOLS, *FILE_WRITE_TOOLS, "Bash")
PLAN_TOOLS = (*FILE_READ_TOOLS, "Bash")
PLAN_COORDINATOR_TOOLS = ("get_task", "post_step", "request_help", "submit_plan")
TRIAGE_COORDINATOR_TOOLS = ("get_task", "post_step", "request_help", "submit_triage")
COORDINATOR_TOOLS = (
    "get_task",
    "claim_paths",
    "renew_lease",
    "release_paths",
    "post_step",
    "post_handoff",
    "request_help",
)
# Never available, even if a future CLI enables them by default.
DENIED_TOOLS = (
    "WebFetch",
    "WebSearch",
    "Task",
    "Agent",
    "NotebookEdit",
    "KillShell",
    "BashOutput",
    "Skill",
    "SlashCommand",
)
# Agents can't edit these whatever their lease says (docs/agents.md, plan "Agent write scope").
PROTECTED_GLOBS = (
    ".git/**",
    ".claude/**",
    ".github/**",
    "AGENTS.md",
    "CLAUDE.md",
    "docs/agents.md",
    "docs/standards/**",
    "sandbox/**",
    "scripts/**",
    "infra/**",
    "backend/migrations/**",
    "**/generated/**",
    "**/uv.lock",
    "**/pnpm-lock.yaml",
)
MAX_COMMAND_CHARS = 2000
_META = frozenset(";&|`$<>(){}\\\n\r")
_CD_PREFIX = re.compile(r"^\s*cd\s+(?P<dir>[A-Za-z0-9._/-]+)\s*&&\s*(?P<rest>.*)$", re.DOTALL)
_CHECK_IDS = re.compile(r"^[a-z0-9][a-z0-9,_-]*$")
_CHECK_SCRIPT = ("scripts/check.sh", "./scripts/check.sh", f"{WORKSPACE}/scripts/check.sh")
_GIT_SUBCOMMANDS = ("status", "diff", "add", "commit")
# Options that redirect git at other repos, run configured programs or write files.
_GIT_DENIED = (
    "-c",
    "-C",
    "--config-env",
    "--git-dir",
    "--work-tree",
    "--exec-path",
    "--namespace",
    "--output",
    "--ext-diff",
    "--textconv",
    "--no-index",
    "--template",
)
_PNPM_SCRIPTS = ("test", "lint", "typecheck")
_PLAN_GIT_SUBCOMMANDS = ("ls-files", "log", "grep", "show", "status", "diff")
# `git grep -O <cmd>` runs a program on the matches.
_PLAN_GIT_DENIED = ("--open-files-in-pager",)
# Triage reproduces with existing tests only: flags without values, then flags taking one.
_TRIAGE_PYTEST_FLAGS = ("-q", "-x", "-v", "-rA", "--tb=short", "--tb=line", "--tb=no")
_TRIAGE_PYTEST_VALUE_FLAGS = ("-k",)
_TRIAGE_VITEST_VALUE_FLAGS = ("-t", "--testNamePattern")
_NO_CACHE = "no:cacheprovider"
_GEN_API = ("scripts/gen-api.sh", "./scripts/gen-api.sh", f"{WORKSPACE}/scripts/gen-api.sh")
API_SPEC = "api/openapi.yaml"
MODIFY_TESTS_LABEL = "modify-tests:"
TEST_DIRS = ("backend/tests/", "web/tests/")
TEST_SUFFIXES = (".test.ts", ".test.tsx")


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str = ""


ALLOW = Decision(True)


def deny(reason: str) -> Decision:
    return Decision(False, reason)


# ------------------------------------------------------------------ paths


def resolve(path: str, cwd: str = WORKSPACE) -> str | None:
    """Normalise ``path`` (relative to ``cwd``); None unless it stays inside the workspace."""
    if not path or "\x00" in path:
        return None
    joined = path if path.startswith("/") else posixpath.join(cwd, path)
    norm = posixpath.normpath(joined)
    if norm == WORKSPACE or norm.startswith(WORKSPACE + "/"):
        return norm
    return None


def relative(path: str) -> str:
    """Repo-relative form of a resolved workspace path ("" for the repo root)."""
    return "" if path == WORKSPACE else path[len(WORKSPACE) + 1 :]


def glob_regex(pattern: str) -> re.Pattern[str]:
    """``**`` spans directories, ``*`` and ``?`` don't; a trailing ``/`` means the whole tree."""
    pat = pattern.strip().lstrip("/")
    if pat.startswith("./"):
        pat = pat[2:]
    if pat.endswith("/"):
        pat += "**"
    out: list[str] = []
    i = 0
    while i < len(pat):
        if pat.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pat.startswith("**", i):
            out.append(".*")
            i += 2
        elif pat[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pat[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pat[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


def matches_any(rel_path: str, globs: Sequence[str]) -> bool:
    return any(glob_regex(g).match(rel_path) for g in globs if g.strip())


def normalise_entry(entry: str) -> str:
    """The board's path-entry form: trimmed, no leading ``./``, no trailing ``/``."""
    text = entry.strip()
    while text.startswith("./"):
        text = text[2:]
    return text.rstrip("/")


def covers(entries: Sequence[str], path: str) -> bool:
    """Exact match, or an entry that is a folder prefix of ``path`` (a partial folder name
    doesn't count). Same rule as the board's ``modify-tests:`` approval."""
    target = normalise_entry(path)
    for entry in entries:
        norm = normalise_entry(entry)
        if norm and (target == norm or target.startswith(norm + "/")):
            return True
    return False


def is_test_path(rel_path: str) -> bool:
    name = rel_path.rsplit("/", 1)[-1]
    return rel_path.startswith(TEST_DIRS) or name.endswith(TEST_SUFFIXES)


def parse_modify_tests(labels: Sequence[object]) -> tuple[str, ...]:
    """Paths from the card's ``modify-tests:<path>[,<path>...]`` labels (maintainer-only)."""
    out: list[str] = []
    for label in labels:
        if not isinstance(label, str) or not label.startswith(MODIFY_TESTS_LABEL):
            continue
        for raw in label[len(MODIFY_TESTS_LABEL) :].split(","):
            entry = normalise_entry(raw)
            if not entry or entry.startswith("/") or ".." in entry.split("/"):
                continue
            if entry not in out:
                out.append(entry)
    return tuple(out)


# ------------------------------------------------------------------ bash


def _check_args(args: Sequence[str]) -> Decision:
    for arg in args:
        if arg.startswith("/") and resolve(arg) is None:
            return deny(f"absolute path outside {WORKSPACE}: {arg[:80]}")
    return ALLOW


def _git(args: Sequence[str]) -> Decision:
    if not args or args[0] not in _GIT_SUBCOMMANDS:
        return deny(f"git: only {', '.join(_GIT_SUBCOMMANDS)} (subcommand first)")
    for arg in args[1:]:
        name = arg.split("=", 1)[0]
        if name in _GIT_DENIED:
            return deny(f"git option not allowed: {name}")
    return _check_args(args[1:])


def _plan_git(args: Sequence[str]) -> Decision:
    if not args or args[0] not in _PLAN_GIT_SUBCOMMANDS:
        return deny(f"git (plan mode): only {', '.join(_PLAN_GIT_SUBCOMMANDS)} (subcommand first)")
    for arg in args[1:]:
        if arg == "--":
            break
        name = arg.split("=", 1)[0]
        if name in _GIT_DENIED or name in _PLAN_GIT_DENIED:
            return deny(f"git option not allowed: {name}")
        if arg.startswith("-") and not arg.startswith("--") and "O" in arg:
            return deny("git option not allowed: -O")
    return _check_args(args[1:])


def _uv(args: Sequence[str]) -> Decision:
    if len(args) < 2 or args[0] != "run":
        return deny("uv: only `uv run pytest|ruff|pyright`")
    tool, rest = args[1], args[2:]
    if tool in ("pytest", "pyright"):
        return _check_args(rest)
    if tool == "ruff" and rest and rest[0] in ("check", "format"):
        return _check_args(rest[1:])
    return deny("uv: only `uv run pytest`, `uv run ruff check|format`, `uv run pyright`")


def _pnpm(args: Sequence[str]) -> Decision:
    rest = args[1:] if args and args[0] == "run" else args
    if not rest or rest[0] not in _PNPM_SCRIPTS:
        return deny(f"pnpm: only {', '.join(_PNPM_SCRIPTS)}")
    return _check_args(rest[1:])


def _check_sh(args: Sequence[str]) -> Decision:
    if list(args) == ["--list"]:
        return ALLOW
    if len(args) == 2 and args[0] == "--only" and _CHECK_IDS.match(args[1]):
        return ALLOW
    if len(args) == 1 and args[0].startswith("--only=") and _CHECK_IDS.match(args[0][7:]):
        return ALLOW
    return deny("check.sh: only `scripts/check.sh --only <ids>` or `--list`")


def _unquoted_meta(command: str) -> bool:
    """True if bash would treat any character as an operator or an expansion.

    Inside single quotes nothing is special; inside double quotes ``$``, backtick and
    backslash still are. Newlines are refused everywhere.
    """
    quote = ""
    for ch in command:
        if ch in "\n\r":
            return True
        if quote == "'":
            quote = "" if ch == "'" else quote
        elif quote == '"':
            if ch == '"':
                quote = ""
            elif ch in "$`\\":
                return True
        elif ch in "'\"":
            quote = ch
        elif ch in _META:
            return True
    return False


def _argv(command: str, cwd: str) -> tuple[str, list[str]] | Decision:
    """The command's working directory and argv, or a denial."""
    if len(command) > MAX_COMMAND_CHARS:
        return deny("command too long")
    rest = command
    match = _CD_PREFIX.match(command)
    if match:
        target = resolve(match["dir"], cwd)
        if target is None:
            return deny("cd outside the workspace")
        cwd, rest = target, match["rest"]
    if _unquoted_meta(rest):
        return deny("shell metacharacters are not allowed (one command, no pipes or expansion)")
    try:
        argv = shlex.split(rest, posix=True)
    except ValueError:
        return deny("unparseable command")
    if not argv:
        return deny("empty command")
    if "=" in argv[0]:
        return deny("environment assignments are not allowed")
    return cwd, argv


def _is_script(prog: str, cwd: str, names: Sequence[str], script: str) -> bool:
    return prog in names or (
        (prog.endswith("/" + script) or prog == script)
        and resolve(prog, cwd) == f"{WORKSPACE}/scripts/{script}"
    )


def bash_allowed(command: str, cwd: str = WORKSPACE, *, gen_api: bool = False) -> Decision:
    """Whether the agent may run ``command`` with the Bash tool. ``gen_api``: the run holds a
    lease on ``api/openapi.yaml``, so ``scripts/gen-api.sh`` (no arguments) may run."""
    parsed = _argv(command, cwd)
    if isinstance(parsed, Decision):
        return parsed
    cwd, argv = parsed
    prog, args = argv[0], argv[1:]
    if prog == "bash" and args and (args[0] in _CHECK_SCRIPT or args[0] in _GEN_API):
        prog, args = args[0], args[1:]
    if prog == "git":
        return _git(args)
    if prog == "uv":
        return _uv(args)
    if prog == "pnpm":
        return _pnpm(args)
    if _is_script(prog, cwd, _CHECK_SCRIPT, "check.sh"):
        return _check_sh(args)
    if _is_script(prog, cwd, _GEN_API, "gen-api.sh"):
        if not gen_api:
            return deny(f"scripts/gen-api.sh: only while you hold a lease on {API_SPEC}")
        return ALLOW if not args else deny("scripts/gen-api.sh: no arguments")
    return deny(
        "not on the Bash allowlist: uv run pytest|ruff|pyright, pnpm test|lint|typecheck, "
        "scripts/check.sh --only <ids>, git status|diff|add|commit"
        + (", scripts/gen-api.sh" if gen_api else "")
    )


def plan_bash_allowed(command: str, cwd: str = WORKSPACE) -> Decision:
    """Plan mode: read-only git only (no tests, no edits, no scripts)."""
    parsed = _argv(command, cwd)
    if isinstance(parsed, Decision):
        return parsed
    _, argv = parsed
    if argv[0] != "git":
        return deny(
            "plan mode is read-only: Bash only for git "
            + "|".join(_PLAN_GIT_SUBCOMMANDS)
            + "; use Read, Glob and Grep for files"
        )
    return _plan_git(argv[1:])


def _triage_targets(
    args: Sequence[str], cwd: str, flags: Sequence[str], value_flags: Sequence[str]
) -> list[str] | Decision:
    """Repo-relative test files named by ``args`` (``path[::node]``), or a denial."""
    files: list[str] = []
    rest = list(args)
    while rest:
        arg = rest.pop(0)
        if arg in value_flags:
            if not rest:
                return deny(f"{arg} needs a value")
            rest.pop(0)
        elif arg == "-p":
            if not rest or rest.pop(0) != _NO_CACHE:
                return deny(f"-p: only -p {_NO_CACHE} (no plugins)")
        elif arg.startswith("-"):
            if arg not in flags:
                return deny(f"option not allowed in triage: {arg.split('=', 1)[0][:40]}")
        else:
            target = resolve(arg.split("::", 1)[0], cwd)
            if target is None:
                return deny(f"test path outside {WORKSPACE}: {arg[:80]}")
            files.append(relative(target))
    if not files:
        return deny("name the existing test file(s) to run; a whole-suite run is refused")
    return files


def _triage_pytest(args: Sequence[str], cwd: str, existing: frozenset[str]) -> Decision:
    if cwd != f"{WORKSPACE}/backend" or list(args[:2]) != ["run", "pytest"]:
        return deny("triage: run backend tests as `cd backend && uv run pytest -q <test file>`")
    files = _triage_targets(args[2:], cwd, _TRIAGE_PYTEST_FLAGS, _TRIAGE_PYTEST_VALUE_FLAGS)
    if isinstance(files, Decision):
        return files
    for rel in files:
        if not rel.startswith("backend/tests/") or rel not in existing:
            return deny(f"triage runs only existing backend/tests/ files: {rel[:80]}")
    return ALLOW


def _triage_vitest(args: Sequence[str], cwd: str, existing: frozenset[str]) -> Decision:
    if cwd != f"{WORKSPACE}/web" or list(args[:2]) != ["vitest", "run"]:
        return deny("triage: run web tests as `cd web && pnpm vitest run <file>.test.tsx`")
    files = _triage_targets(args[2:], cwd, (), _TRIAGE_VITEST_VALUE_FLAGS)
    if isinstance(files, Decision):
        return files
    for rel in files:
        is_unit = rel.startswith("web/src/") and rel.endswith(TEST_SUFFIXES)
        if not is_unit or rel not in existing:
            return deny(f"triage runs only existing web/src/**/*.test.ts(x) files: {rel[:80]}")
    return ALLOW


def triage_bash_allowed(
    command: str, cwd: str = WORKSPACE, existing_tests: frozenset[str] = frozenset()
) -> Decision:
    """Triage mode: read-only git, plus existing tests (named files only) to reproduce."""
    parsed = _argv(command, cwd)
    if isinstance(parsed, Decision):
        return parsed
    cwd, argv = parsed
    prog, args = argv[0], argv[1:]
    if prog == "git":
        return _plan_git(args)
    if prog == "uv":
        return _triage_pytest(args, cwd, existing_tests)
    if prog == "pnpm":
        return _triage_vitest(args, cwd, existing_tests)
    return deny(
        "triage mode is read-only: Bash only for git "
        + "|".join(_PLAN_GIT_SUBCOMMANDS)
        + ", `cd backend && uv run pytest -q <existing test file>` and "
        "`cd web && pnpm vitest run <existing *.test.tsx>`; use Read, Glob and Grep for files"
    )


# ------------------------------------------------------------------ file tools


def _str(value: object) -> str:
    return value if isinstance(value, str) else ""


def file_tool_allowed(
    tool: str,
    tool_input: Mapping[str, object],
    leases: Sequence[str],
    *,
    existing_tests: frozenset[str] = frozenset(),
    modify_tests: Sequence[str] = (),
) -> Decision:
    if tool in FILE_READ_TOOLS:
        key = "file_path" if tool == "Read" else "path"
        raw = _str(tool_input.get(key))
        if raw and resolve(raw) is None:
            return deny(f"{tool}: only inside {WORKSPACE}")
        pattern = _str(tool_input.get("pattern")) if tool == "Glob" else ""
        escapes = pattern.startswith("/") or ".." in pattern.split("/")
        if pattern and escapes and resolve(pattern) is None:
            return deny("Glob: pattern must stay inside the workspace")
        return ALLOW
    if tool in FILE_WRITE_TOOLS:
        target = resolve(_str(tool_input.get("file_path")))
        if target is None:
            return deny(f"{tool}: only inside {WORKSPACE}")
        rel = relative(target)
        if not rel or matches_any(rel, PROTECTED_GLOBS):
            return deny(f"{tool}: {rel or '/'} is protected")
        if not leases:
            return deny(f"{tool}: claim the paths first (mcp__coordinator__claim_paths)")
        if not matches_any(rel, leases):
            return deny(f"{tool}: {rel} is outside your granted leases")
        if is_test_path(rel) and rel in existing_tests and not covers(modify_tests, rel):
            return deny(
                f"{tool}: {rel} is an existing test; changing it needs a maintainer's "
                "modify-tests: label on the card covering it (ask with request_help)"
            )
        return ALLOW
    return deny(f"{tool}: not a file tool")


def tool_allowed(
    tool: str,
    tool_input: Mapping[str, object],
    leases: Sequence[str],
    *,
    plan: bool = False,
    triage: bool = False,
    existing_tests: frozenset[str] = frozenset(),
    modify_tests: Sequence[str] = (),
) -> Decision:
    """The single entry point used by the runner's ``can_use_tool`` callback."""
    if tool.startswith(MCP_PREFIX):
        name = tool[len(MCP_PREFIX) :]
        if triage:
            allowed = TRIAGE_COORDINATOR_TOOLS
        else:
            allowed = PLAN_COORDINATOR_TOOLS if plan else COORDINATOR_TOOLS
        return ALLOW if name in allowed else deny(f"unknown coordinator tool {name}")
    if tool == "Bash":
        command = _str(tool_input.get("command"))
        if triage:
            return triage_bash_allowed(command, existing_tests=existing_tests)
        if plan:
            return plan_bash_allowed(command)
        return bash_allowed(command, gen_api=matches_any(API_SPEC, leases))
    if triage and tool in FILE_WRITE_TOOLS:
        return deny(f"{tool}: triage is read-only; report what you found in submit_triage")
    if plan and tool in FILE_WRITE_TOOLS:
        return deny(f"{tool}: plan mode is read-only; describe changes in the plan instead")
    if tool in FILE_READ_TOOLS or tool in FILE_WRITE_TOOLS:
        return file_tool_allowed(
            tool, tool_input, leases, existing_tests=existing_tests, modify_tests=modify_tests
        )
    return deny(f"tool {tool} is not available in the sandbox")


# ------------------------------------------------------------------ run tracking


USAGE_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)


@dataclass
class RunTracker:
    """Budget (from the message stream) plus leases, steps and handoff (from the tools)."""

    token_budget: int
    tokens_used: int = 0
    steps_posted: int = 0
    leases: list[str] = field(default_factory=list[str])
    handoff: dict[str, object] | None = None
    denials: int = 0
    help_requested: bool = False
    plan_mode: bool = False
    plan_submitted: bool = False
    triage_mode: bool = False
    triage_submitted: bool = False
    # The open cards' ids from get_task (triage runs): the only valid duplicate_of values.
    open_cards: frozenset[str] = frozenset()
    modify_tests: tuple[str, ...] = ()
    existing_tests: frozenset[str] = frozenset()
    _seen_messages: set[str] = field(default_factory=set[str])

    def on_usage(self, message_id: str | None, usage: Mapping[str, object] | None) -> None:
        """One API response can arrive as several messages; count each id once."""
        if usage is None or (message_id is not None and message_id in self._seen_messages):
            return
        if message_id is not None:
            self._seen_messages.add(message_id)
        for name in USAGE_FIELDS:
            value = usage.get(name)
            if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                self.tokens_used += value

    @property
    def over_budget(self) -> bool:
        return self.tokens_used >= self.token_budget

    def add_leases(self, paths: Sequence[str]) -> None:
        """Paths the coordinator GRANTED (from the claim response, not the request)."""
        self.leases.extend(p for p in paths if p not in self.leases)

    def release_leases(self, paths: Sequence[str] | None) -> None:
        """``None`` releases everything, like the coordinator."""
        self.leases = [] if paths is None else [p for p in self.leases if p not in paths]

    def result(self, stop: str, turns: int) -> dict[str, object]:
        """What the entrypoint feeds to handoff.py (no transcript, no tool output)."""
        return {
            "stop": stop,
            "turns": turns,
            "tokens_used": self.tokens_used,
            "token_budget": self.token_budget,
            "steps_posted": self.steps_posted,
            "posted_handoff": self.handoff is not None,
            "handoff": self.handoff or {},
            "denied_tool_calls": self.denials,
            "help_requested": self.help_requested,
            "plan_submitted": self.plan_submitted,
            "triage_submitted": self.triage_submitted,
        }
