"""Mint a per-task token from LLM_PROXY_SECRET in the environment (prints only the token).

python -m llm_proxy.mint --task-id T-1 --ttl 3600 --budget 200000
"""

from __future__ import annotations

import argparse
import os
import sys

from .tokens import mint


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="llm_proxy.mint")
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--ttl", type=int, default=3600, help="seconds (negative = expired)")
    parser.add_argument("--budget", type=int, default=200_000, help="total tokens for the task")
    args = parser.parse_args(argv)
    secret = os.environ.get("LLM_PROXY_SECRET", "").encode()
    try:
        print(mint(secret, args.task_id, args.ttl, args.budget))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
