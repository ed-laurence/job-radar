"""Tool policy for the writer agent (stdlib only; advisory, the container is the boundary).

Decides which tool calls the agent may make inside the sandbox:

- ``bash_allowed``: Bash only for an allowlist of test, lint and git commands, no shell
  metacharacters, and at most one ``cd <dir> &&`` prefix inside the workspace.
- ``file_tool_allowed``: Read/Glob/Grep only inside the workspace (so ``/proc/self/environ`` and
  the tokens in it stay out of reach); Edit/Write only on paths covered by a claimed lease and
  never on protected paths.
- ``RunTracker``: counts tokens the way llm-proxy does; the coordinator tools record granted
  leases, steps and the final ``post_handoff`` in it.

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


def bash_allowed(command: str, cwd: str = WORKSPACE) -> Decision:
    """Whether the agent may run ``command`` with the Bash tool."""
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
    prog, args = argv[0], argv[1:]
    if prog == "bash" and args and args[0] in _CHECK_SCRIPT:
        prog, args = args[0], args[1:]
    if prog == "git":
        return _git(args)
    if prog == "uv":
        return _uv(args)
    if prog == "pnpm":
        return _pnpm(args)
    if prog in _CHECK_SCRIPT or (
        (prog.endswith("/check.sh") or prog == "check.sh")
        and resolve(prog, cwd) == f"{WORKSPACE}/scripts/check.sh"
    ):
        return _check_sh(args)
    return deny(
        "not on the Bash allowlist: uv run pytest|ruff|pyright, pnpm test|lint|typecheck, "
        "scripts/check.sh --only <ids>, git status|diff|add|commit"
    )


# ------------------------------------------------------------------ file tools


def _str(value: object) -> str:
    return value if isinstance(value, str) else ""


def file_tool_allowed(
    tool: str, tool_input: Mapping[str, object], leases: Sequence[str]
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
        return ALLOW
    return deny(f"{tool}: not a file tool")


def tool_allowed(tool: str, tool_input: Mapping[str, object], leases: Sequence[str]) -> Decision:
    """The single entry point used by the runner's ``can_use_tool`` callback."""
    if tool.startswith(MCP_PREFIX):
        name = tool[len(MCP_PREFIX) :]
        return ALLOW if name in COORDINATOR_TOOLS else deny(f"unknown coordinator tool {name}")
    if tool == "Bash":
        return bash_allowed(_str(tool_input.get("command")))
    if tool in FILE_READ_TOOLS or tool in FILE_WRITE_TOOLS:
        return file_tool_allowed(tool, tool_input, leases)
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
        }
