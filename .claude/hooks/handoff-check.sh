#!/usr/bin/env bash
# SubagentStop hook: reject a repo subagent's final message when it breaks the message caps
# in AGENTS.md / docs/agents.md, so the subagent rewrites it before the caller reads it.
#
# Enabled in .claude/settings.json:
#   "hooks": { "SubagentStop": [ { "hooks": [ { "type": "command",
#     "command": "bash \"$CLAUDE_PROJECT_DIR/.claude/hooks/handoff-check.sh\"" } ] } ] }
#
# Handoff agents (Output = handoff): ~400 tokens max (chars/4), fields status, summary,
#   changed_paths, tests, rubric_checked, open_questions, skipped_checks; summary <= 80 words;
#   open_questions <= 3; no code fences or diffs.
# Findings agents (Output = findings JSON): <= 10 findings, a verdict, ~1500 tokens max.
# Other agent types (Explore, general-purpose, ...) are not checked.
# Problems: exit 2 with the list on stderr (the subagent sees it and continues). It blocks once:
# when stop_hook_active is set (already blocked), the stop goes through.
set -euo pipefail
# shellcheck source=_common.sh
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

rc=0
printf '%s' "$HOOK_INPUT" | run_py -c '
import json, re, sys

HANDOFF = {"backend-engineer", "frontend-engineer", "qa-expert", "prompt-engineer",
           "documentation-engineer", "dev-lead", "cloud-architect", "devops-engineer",
           "coordinator-developer", "performance-engineer", "postgres-pro"}
FINDINGS = {"code-reviewer", "security-auditor", "privacy-reviewer", "ui-ux-tester", "redteam"}
FIELDS = ["status", "summary", "changed_paths", "tests", "rubric_checked",
          "open_questions", "skipped_checks"]

try:
    ev = json.loads(sys.stdin.read() or "{}")
except ValueError:
    sys.exit(0)
agent = str(ev.get("agent_type") or "")
if agent not in HANDOFF | FINDINGS or ev.get("stop_hook_active"):
    sys.exit(0)

def last_text_from_transcript(path):
    text = ""
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if entry.get("type") != "assistant":
                    continue
                content = (entry.get("message") or {}).get("content")
                if isinstance(content, str):
                    parts = [content]
                else:
                    parts = [c.get("text", "") for c in content or [] if isinstance(c, dict) and c.get("type") == "text"]
                joined = "\n".join(p for p in parts if p.strip())
                if joined:
                    text = joined
    except OSError:
        pass
    return text

msg = ev.get("last_assistant_message") or ""
if not msg and ev.get("agent_transcript_path"):
    msg = last_text_from_transcript(ev["agent_transcript_path"])
if not msg.strip():
    sys.exit(0)

problems = []
tokens = len(msg) // 4

# Field key at line start, tolerating "- ", "**", backticks and quotes: e.g. **summary**: / "tests":
key_re = re.compile(r"(?im)^[\s>*\-]*[*`\"]*(" + "|".join(FIELDS) + r"|verdict)[*`\"]*\s*:")

if agent in HANDOFF:
    if tokens > 400:
        problems.append(f"~{tokens} tokens (chars/4); the cap is 400. Cut prose, reference paths.")
    found = {m.group(1).lower() for m in key_re.finditer(msg)}
    missing = [f for f in FIELDS if f not in found]
    if missing:
        problems.append("missing fields: " + ", ".join(missing))
    marks = list(key_re.finditer(msg))
    for i, m in enumerate(marks):
        body = msg[m.end(): marks[i + 1].start() if i + 1 < len(marks) else len(msg)]
        name = m.group(1).lower()
        if name == "summary" and len(body.split()) > 80:
            problems.append(f"summary is {len(body.split())} words; the cap is 80")
        if name == "open_questions":
            n = len(re.findall(r"(?m)^\s*(?:[-*]|\d+[.)])\s+\S", body))
            if n > 3:
                problems.append(f"{n} open_questions; the cap is 3")
    if "```" in msg:
        problems.append("contains a code block; handoffs never include code (reference paths)")
    if re.search(r"(?m)^(?:@@ |\+\+\+ [ab]/|--- [ab]/|diff --git )", msg):
        problems.append("contains a diff; reference changed_paths instead")
else:
    if tokens > 1500:
        problems.append(f"~{tokens} tokens (chars/4); findings cap is 10 x (40 + 30 words).")
    n = len(re.findall(r"\"?severity\"?\s*:", msg))
    if n > 10:
        problems.append(f"{n} findings; the cap is 10 (keep the most severe)")
    if not re.search(r"\"?verdict\"?\s*:\s*\"?(approve|changes_requested)", msg):
        problems.append("missing verdict: approve|changes_requested")

if problems:
    kind = "handoff" if agent in HANDOFF else "findings"
    sys.stderr.write(f"handoff-check: your final message breaks the {kind} format ({agent}, see AGENTS.md / your Output section):\n")
    for p in problems:
        sys.stderr.write(f"- {p}\n")
    sys.stderr.write("Reply again with only the corrected " + kind + ".\n")
    sys.exit(2)
' || rc=$?
# 2 = block; any other failure of the checker itself must never block a subagent.
[[ $rc -eq 2 ]] && exit 2
exit 0
