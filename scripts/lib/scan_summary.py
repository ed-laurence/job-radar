"""Summarise scanner JSON reports as one line per finding: rule, file:line, severity.

    python scan_summary.py gitleaks|semgrep <report.json>

Prints no matched content (gitleaks secrets, semgrep code lines), so the output is safe to pass
to the security agent and to post in a check run. Exit 1 if the report has findings
(for semgrep: any ERROR severity), 0 otherwise, 2 if the report is unreadable.
"""

from __future__ import annotations

import json
import sys


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[1] not in {"gitleaks", "semgrep"}:
        print(__doc__, file=sys.stderr)
        return 2
    try:
        with open(argv[2], encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        print(f"{argv[1]}: report unreadable")
        return 2
    failing = False
    if argv[1] == "gitleaks":
        for item in data or []:
            print(f"{item.get('RuleID')} {item.get('File')}:{item.get('StartLine')}")
            failing = True
    else:
        for res in data.get("results", []):
            severity = res.get("extra", {}).get("severity", "")
            print(f"{res.get('check_id')} {res.get('path')}:{res.get('start', {}).get('line')} {severity}")
            failing = failing or severity == "ERROR"
    return 1 if failing else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
