"""Mint a coordinator token.

    uv run python -m app.coordinator.mint --task <id> --run <id> --ttl <s>

Reads `COORDINATOR_TOKEN_KEY` from the environment (through `CoordinatorSettings`) and prints
only the token. Errors go to stderr and never include the key.
"""

from __future__ import annotations

import argparse
import sys
import uuid
from typing import TextIO

from app.coordinator.config import CoordinatorSettings
from app.coordinator.tokens import MAX_TTL_SECONDS, mint


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.coordinator.mint")
    parser.add_argument("--task", required=True, type=uuid.UUID, help="task id")
    parser.add_argument("--run", required=True, type=uuid.UUID, help="active run id")
    parser.add_argument(
        "--ttl", type=int, default=3600, help=f"lifetime in seconds (1..{MAX_TTL_SECONDS})"
    )
    return parser


def main(
    argv: list[str] | None = None,
    *,
    settings: CoordinatorSettings | None = None,
    out: TextIO = sys.stdout,
    err: TextIO = sys.stderr,
) -> int:
    try:
        args = _parser().parse_args(argv)
    except SystemExit as stop:  # argparse already printed the usage to stderr
        return stop.code if isinstance(stop.code, int) else 2
    try:
        config = settings if settings is not None else CoordinatorSettings()
        token = mint(config.token_key(), task_id=args.task, run_id=args.run, ttl_s=args.ttl)
    except ValueError as error:
        err.write(f"mint: {error}\n")
        return 2
    out.write(token + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
