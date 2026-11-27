"""The GCS storage adapter against a mocked JSON API (respx): upload, download, delete, 404s,
bearer auth, encoded object names, refused keys, and errors that don't echo the response."""

from __future__ import annotations

import httpx
import pytest
import respx

from app.adapters.http import StaticAccessTokens, allowed_hosts, allowlisted_client
from app.adapters.storage_gcs import GCS_HOST, GcsStorage
from app.domain.errors import NotFound, UpstreamUnavailable

BASE = f"https://{GCS_HOST}"
OBJECT = f"{BASE}/storage/v1/b/bucket-xxxx/o/users%2Fu1%2Fprofile%2Fcv.pdf"


def _storage() -> GcsStorage:
    http = allowlisted_client(allowed_hosts(GCS_HOST))
    return GcsStorage(http, StaticAccessTokens("token-xxxx"), "bucket-xxxx")


@respx.mock
async def test_put_get_delete_round_trip() -> None:
    upload = respx.post(f"{BASE}/upload/storage/v1/b/bucket-xxxx/o").mock(
        return_value=httpx.Response(200, json={"name": "users/u1/profile/cv.pdf"})
    )
    download = respx.get(OBJECT, params={"alt": "media"}).mock(
        return_value=httpx.Response(200, content=b"%PDF-1.7")
    )
    remove = respx.delete(OBJECT).mock(return_value=httpx.Response(204))
    storage = _storage()
    await storage.put("users/u1/profile/cv.pdf", b"%PDF-1.7", "application/pdf")
    sent = upload.calls.last.request
    assert sent.headers["Authorization"] == "Bearer token-xxxx"
    assert sent.headers["Content-Type"] == "application/pdf"
    assert sent.url.params["uploadType"] == "media"
    assert sent.url.params["name"] == "users/u1/profile/cv.pdf"
    assert sent.content == b"%PDF-1.7"
    assert await storage.get("users/u1/profile/cv.pdf") == b"%PDF-1.7"
    assert download.called
    await storage.delete("users/u1/profile/cv.pdf")
    assert remove.called


@respx.mock
async def test_missing_objects() -> None:
    respx.get(OBJECT).mock(return_value=httpx.Response(404))
    respx.delete(OBJECT).mock(return_value=httpx.Response(404))
    storage = _storage()
    with pytest.raises(NotFound):
        await storage.get("users/u1/profile/cv.pdf")
    await storage.delete("users/u1/profile/cv.pdf")  # already gone is fine


@respx.mock
async def test_upstream_errors_do_not_echo_the_body() -> None:
    respx.get(OBJECT).mock(return_value=httpx.Response(500, text="secret object detail"))
    with pytest.raises(UpstreamUnavailable) as caught:
        await _storage().get("users/u1/profile/cv.pdf")
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize("key", ["../etc/passwd", "/abs", "users/../x", "", "a//b"])
async def test_keys_that_could_escape_are_refused(key: str) -> None:
    with pytest.raises(ValueError, match="invalid storage key"):
        await _storage().get(key)
