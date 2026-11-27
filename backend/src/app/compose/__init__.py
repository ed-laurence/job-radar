"""Feature-area composition: each area wires itself, so no area edits `app.main`.

An area adds one module `app/compose/<area>.py` exposing::

    def install(app: FastAPI, container: Container) -> None: ...

which builds the area's adapters and domain services from the container, stores them on
`app.state.<area>`, and includes the area's routers. Modules are installed in name order.
This package sits above `app.main` in the import-linter layer contract.
"""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Callable
from typing import TYPE_CHECKING, Protocol, cast

if TYPE_CHECKING:
    from fastapi import FastAPI

    from app.main import Container


class _AreaModule(Protocol):
    install: Callable[[FastAPI, Container], None]


def area_names() -> list[str]:
    return sorted(info.name for info in pkgutil.iter_modules(__path__) if not info.ispkg)


def install_all(app: FastAPI, container: Container) -> None:
    for name in area_names():
        module = cast("_AreaModule", importlib.import_module(f"{__name__}.{name}"))
        module.install(app, container)
