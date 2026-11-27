"""Google Cloud Storage (implements `domain.ports.Storage`) over the JSON API with httpx and
Application Default Credentials, so it works over Private Google Access without the SDK.

Keys are server-made (`users/<id>/...`) and checked like the local adapter's; object names are
URL-encoded. Nothing about the content is logged.
"""

from __future__ import annotations

import re
from urllib.parse import quote

import httpx

from app.adapters.http import AccessTokenSource
from app.domain.errors import NotFound, UpstreamUnavailable

GCS_HOST = "storage.googleapis.com"
_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(?:/[A-Za-z0-9][A-Za-z0-9._-]*)*$")
_NOT_FOUND = 404


def _check(key: str) -> str:
    if not _KEY.match(key) or ".." in key.split("/"):
        raise ValueError("invalid storage key")
    return key


class GcsStorage:
    def __init__(
        self, http: httpx.AsyncClient, tokens: AccessTokenSource, bucket: str, host: str = GCS_HOST
    ) -> None:
        self._http = http
        self._tokens = tokens
        self._bucket = quote(bucket, safe="")
        self._base = f"https://{host}"

    async def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {await self._tokens.token()}"}

    def _object(self, key: str) -> str:
        name = quote(_check(key), safe="")
        return f"{self._base}/storage/v1/b/{self._bucket}/o/{name}"

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        if not content_type:
            raise ValueError("content type required")
        url = f"{self._base}/upload/storage/v1/b/{self._bucket}/o"
        headers = {**await self._headers(), "Content-Type": content_type}
        params = {"uploadType": "media", "name": _check(key)}
        response = await self._http.post(url, params=params, content=data, headers=headers)
        _raise_for(response)

    async def get(self, key: str) -> bytes:
        response = await self._http.get(
            self._object(key), params={"alt": "media"}, headers=await self._headers()
        )
        if response.status_code == _NOT_FOUND:
            raise NotFound("Object not found.")
        _raise_for(response)
        return response.content

    async def delete(self, key: str) -> None:
        response = await self._http.delete(self._object(key), headers=await self._headers())
        if response.status_code == _NOT_FOUND:
            return
        _raise_for(response)


def _raise_for(response: httpx.Response) -> None:
    if response.is_success:
        return
    # The body may echo the object name: report only the status.
    raise UpstreamUnavailable(f"Storage request failed ({response.status_code}).")
