"""Mint a per-task jrt2 token from LLM_PROXY_SECRET in the environment (prints only the token).

python -m llm_proxy.mint --task-id T-1 --ttl 3600 --budget-usd 2.00
python -m llm_proxy.mint --task-id T-1 --ttl 3600 --usd-micro 2000000
"""

from __future__ import annotations

import argparse
import os
import sys

from .pricing import usd_to_micro
from .tokens import mint


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="llm_proxy.mint")
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--ttl", type=int, default=3600, help="seconds (negative = expired)")
    budget = parser.add_mutually_exclusive_group()
    budget.add_argument("--budget-usd", default=None, help="task budget in USD (default 2.00)")
    budget.add_argument("--usd-micro", type=int, default=None, help="task budget in micro-USD")
    args = parser.parse_args(argv)
    secret = os.environ.get("LLM_PROXY_SECRET", "").encode()
    try:
        usd_micro = (
            args.usd_micro
            if args.usd_micro is not None
            else usd_to_micro(args.budget_usd or "2.00")
        )
        print(mint(secret, args.task_id, args.ttl, usd_micro))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
