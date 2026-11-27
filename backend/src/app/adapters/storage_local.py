"""Local-disk object storage (implements `domain.ports.Storage`) for development; GCS in prod.

Keys are server-made (`users/<id>/...`); anything that could escape the root is refused.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from app.domain.errors import NotFound

_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(?:/[A-Za-z0-9][A-Za-z0-9._-]*)*$")


class LocalStorage:
    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def _path(self, key: str) -> Path:
        if not _KEY.match(key) or ".." in key.split("/"):
            raise ValueError("invalid storage key")
        path = (self._root / key).resolve()
        if not path.is_relative_to(self._root):
            raise ValueError("invalid storage key")
        return path

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        if not content_type:
            raise ValueError("content type required")
        path = self._path(key)
        await asyncio.to_thread(_write, path, data)

    async def get(self, key: str) -> bytes:
        path = self._path(key)
        try:
            return await asyncio.to_thread(path.read_bytes)
        except FileNotFoundError:
            raise NotFound("Object not found.") from None

    async def delete(self, key: str) -> None:
        path = self._path(key)
        await asyncio.to_thread(path.unlink, missing_ok=True)


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(data)
    temporary.replace(path)
