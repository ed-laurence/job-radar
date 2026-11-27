"""The scheduled scan: `python -m app.jobs.scan` (Cloud Scheduler, every 15 minutes in prod).
`python -m app.jobs.feed_replay` is kept as an alias for the old job name."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import sys

from app.compose.scan import scan_service
from app.domain.jobs.scan import SCHEDULE, ScanRunning
from app.domain.ports.scan import ScanRunRecord
from app.main import build_container
from app.settings import Settings


async def run_once(settings: Settings) -> ScanRunRecord | None:
    container = build_container(settings)
    try:
        return await scan_service(container).run(SCHEDULE)
    except ScanRunning:
        return None
    finally:
        await container.aclose()


def main() -> int:
    run = asyncio.run(run_once(Settings()))
    if run is None:
        sys.stdout.write(json.dumps({"skipped": "scan-running"}) + "\n")
        return 0
    sys.stdout.write(json.dumps(dataclasses.asdict(run), default=str) + "\n")
    return 1 if run.status == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
