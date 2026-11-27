"""The campaign plan from YAML (`CAMPAIGN_CONFIG`), re-read when the file changes."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import cast

import yaml


class YamlPlanSource:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._cached: tuple[float, Mapping[str, object]] | None = None

    def load(self) -> Mapping[str, object]:
        try:
            stamp = self._path.stat().st_mtime
        except OSError as exc:
            raise ValueError("The campaign plan file can't be read.") from exc
        if self._cached is not None and self._cached[0] == stamp:
            return self._cached[1]
        loaded: object = yaml.safe_load(self._path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise TypeError("The campaign plan must be a mapping.")
        plan = cast("dict[str, object]", loaded)
        self._cached = (stamp, plan)
        return plan
