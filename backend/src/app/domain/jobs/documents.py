"""Profile documents (spec tag Settings): the technical and leadership CVs and the LinkedIn
profile PDF, ported from job-seeker's `profile_store.py` (logic only).

Every upload is untrusted input. It must be a real PDF (magic bytes), at most 5 MB and 20 pages,
and readable (encrypted or broken PDFs are refused). Its extracted text passes the content guard
and is de-identified before it is stored for prompts (SEC-04, SEC-05). The content is never
logged. Files and the per-user manifest live in object storage under a key derived from the
signed-in user and a random id; the client never names a key. A replaced file is archived.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import unicodedata
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, cast

from app.domain.errors import (
    FieldError,
    GuardBlocked,
    NotFound,
    PayloadTooLarge,
    ValidationFailed,
)
from app.domain.identity import Principal
from app.domain.ports import PII, Clock, ContentGuard, Ids, Storage

SLOTS = ("technical", "leadership", "linkedin_profile")
MAX_PDF_BYTES = 5 * 1024 * 1024
MAX_PAGES = 20
MAX_TEXT_CHARS = 60_000
# Each document's share of a prompt; the rest of the text stays stored but unused.
PROMPT_TEXT_CHARS = 20_000
MAX_ARCHIVED = 5
PDF_MAGIC = b"%PDF-"

type JsonMap = Mapping[str, object]


@dataclass(frozen=True)
class PdfContent:
    pages: int
    text: str


class PdfText(Protocol):
    """Text extraction from an untrusted PDF, bounded in time and memory. Raises
    `ValidationFailed` for an encrypted, broken or over-long document."""

    async def extract(self, data: bytes, max_pages: int) -> PdfContent: ...


@dataclass(frozen=True, kw_only=True)
class Readability:
    sections: int
    roles: int
    bullets: int
    ok: bool


@dataclass(frozen=True, kw_only=True)
class DocumentInfo:
    slot: str
    filename: str
    uploaded_at: datetime
    pages: int
    size_bytes: int
    readability: Readability
    archived_versions: int


@dataclass(frozen=True, kw_only=True)
class StoredFile:
    filename: str
    data: bytes


@dataclass(frozen=True, kw_only=True)
class ImportSummary:
    filename: str
    imported_at: datetime
    imported: int
    updated: int
    skipped: int


# ------------------------------------------------------------------ pure helpers

_HEADINGS = frozenset(
    {
        "summary",
        "profile",
        "professional summary",
        "about",
        "experience",
        "work experience",
        "professional experience",
        "employment",
        "employment history",
        "career history",
        "education",
        "qualifications",
        "skills",
        "key skills",
        "technical skills",
        "core competencies",
        "projects",
        "certifications",
        "licenses & certifications",
        "achievements",
        "awards",
        "publications",
        "languages",
        "volunteering",
        "volunteer experience",
        "interests",
        "referees",
        "references",
        "top skills",
    }
)
_DASHES = "-" + chr(0x2013) + chr(0x2014)
_YEAR_RANGE = re.compile(
    rf"(?:19|20)\d{{2}}\s*(?:[{_DASHES}]|to)\s*(?:(?:19|20)\d{{2}}|present|current|now)",
    re.IGNORECASE,
)
# Bullet glyphs: bullet, small square, white bullet, triangular, hyphen bullet, black circle.
_BULLETS = (*(chr(c) for c in (0x2022, 0x25AA, 0x25E6, 0x2023, 0x2043, 0x25CF)), "- ", "* ")


def readability(text: str) -> Readability:
    """A cheap check that extracted text looks like a CV: section headings, role lines with a
    date range, and bullet points. `ok` means scoring and kits can use it."""
    sections = roles = bullets = 0
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.rstrip(":").lower() in _HEADINGS:
            sections += 1
        if _YEAR_RANGE.search(line):
            roles += 1
        if line.startswith(_BULLETS):
            bullets += 1
    ok = sections >= 2 and (roles >= 1 or bullets >= 3)
    return Readability(sections=sections, roles=roles, bullets=bullets, ok=ok)


_UNSAFE_NAME = re.compile(r"[^A-Za-z0-9._ ()-]+")


def sanitise_filename(name: str, default: str = "document.pdf", suffix: str = ".pdf") -> str:
    """A display-safe file name: no path, control or shell characters, at most 120 chars, with
    the expected suffix."""
    base = re.split(r"[\\/]", unicodedata.normalize("NFKC", name))[-1]
    cleaned = _UNSAFE_NAME.sub("_", base).strip(" ._")
    cleaned = re.sub(r"_+", "_", cleaned)
    if not cleaned:
        return default
    stem = cleaned[: -len(suffix)] if cleaned.lower().endswith(suffix) else cleaned
    return stem[: 120 - len(suffix)].rstrip(" ._") + suffix if stem else default


def decode_upload(data: str, max_bytes: int, pointer: str = "/data") -> bytes:
    """Strict base64 to bytes, refusing more than `max_bytes` before and after decoding."""
    if len(data) > (max_bytes + 2) // 3 * 4 + 4:
        raise PayloadTooLarge("That file is too large.")
    try:
        raw = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError):
        raise ValidationFailed(
            "That file couldn't be read.", errors=[FieldError(pointer, "Not valid base64.")]
        ) from None
    if len(raw) > max_bytes:
        raise PayloadTooLarge("That file is too large.")
    if not raw:
        raise ValidationFailed("That file is empty.", errors=[FieldError(pointer, "Empty.")])
    return raw


def check_slot(slot: str) -> str:
    if slot not in SLOTS:
        raise NotFound("That document slot doesn't exist.")
    return slot


# ------------------------------------------------------------------ manifest


def _map(value: object) -> JsonMap:
    return cast("JsonMap", value) if isinstance(value, Mapping) else {}


def _int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _str(value: object) -> str:
    return value if isinstance(value, str) else ""


def _info(slot: str, entry: JsonMap) -> DocumentInfo:
    check = _map(entry.get("readability"))
    archived = entry.get("archived")
    return DocumentInfo(
        slot=slot,
        filename=_str(entry.get("filename")) or "document.pdf",
        uploaded_at=datetime.fromisoformat(_str(entry.get("uploaded_at"))),
        pages=_int(entry.get("pages")),
        size_bytes=_int(entry.get("size_bytes")),
        readability=Readability(
            sections=_int(check.get("sections")),
            roles=_int(check.get("roles")),
            bullets=_int(check.get("bullets")),
            ok=check.get("ok") is True,
        ),
        archived_versions=len(cast("list[object]", archived)) if isinstance(archived, list) else 0,
    )


class ManifestStore(Protocol):
    """The per-user document manifest (`profile_documents`, under RLS)."""

    async def load(self, user_id: uuid.UUID) -> dict[str, object] | None:
        """None when the user has no manifest row yet."""
        ...

    async def save(self, user_id: uuid.UUID, manifest: Mapping[str, object]) -> None: ...


class ProfileDocumentService:
    def __init__(
        self,
        storage: Storage,
        pdf: PdfText,
        guard: ContentGuard,
        pii: PII,
        clock: Clock,
        ids: Ids,
        manifests: ManifestStore | None = None,
    ) -> None:
        self._manifests = manifests
        self._storage = storage
        self._pdf = pdf
        self._guard = guard
        self._pii = pii
        self._clock = clock
        self._ids = ids

    # -- keys: derived from the authenticated user only (never from the client)

    @staticmethod
    def _prefix(user_id: uuid.UUID) -> str:
        return f"users/{user_id}/profile"

    def _manifest_key(self, user_id: uuid.UUID) -> str:
        return f"{self._prefix(user_id)}/manifest.json"

    async def _load(self, user_id: uuid.UUID) -> dict[str, object]:
        if self._manifests is not None:
            stored = await self._manifests.load(user_id)
            if stored is not None:
                return stored
        # No row yet: read a manifest written to storage before `profile_documents` existed.
        return await self._load_legacy(user_id)

    async def _load_legacy(self, user_id: uuid.UUID) -> dict[str, object]:
        try:
            raw = await self._storage.get(self._manifest_key(user_id))
        except NotFound:
            return {}
        try:
            parsed: object = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return {}
        return dict(_map(parsed))

    async def _save(self, user_id: uuid.UUID, manifest: Mapping[str, object]) -> None:
        if self._manifests is not None:
            await self._manifests.save(user_id, manifest)
            return
        body = json.dumps(manifest, sort_keys=True).encode("utf-8")
        await self._storage.put(self._manifest_key(user_id), body, "application/json")

    @staticmethod
    def _documents(manifest: Mapping[str, object]) -> dict[str, JsonMap]:
        return {k: _map(v) for k, v in _map(manifest.get("documents")).items() if k in SLOTS}

    # -- documents

    async def list_documents(self, principal: Principal) -> list[DocumentInfo]:
        documents = self._documents(await self._load(principal.user_id))
        return [
            _info(slot, documents[slot])
            for slot in SLOTS
            if slot in documents and documents[slot].get("key")
        ]

    async def upload(
        self, principal: Principal, slot: str, filename: str, data: str
    ) -> DocumentInfo:
        check_slot(slot)
        raw = decode_upload(data, MAX_PDF_BYTES)
        if not raw.startswith(PDF_MAGIC):
            raise ValidationFailed(
                "That isn't a PDF.", errors=[FieldError("/data", "Upload a PDF file.")]
            )
        content = await self._pdf.extract(raw, MAX_PAGES)
        if content.pages > MAX_PAGES:
            raise ValidationFailed(
                "That PDF is too long.", errors=[FieldError("/data", "At most 20 pages.")]
            )
        text = content.text[:MAX_TEXT_CHARS]
        verdict = await self._guard.inspect_input(text) if text.strip() else None
        if verdict is not None and verdict.is_blocked:
            raise GuardBlocked("This document can't be used.")
        safe_text = await self._pii.deidentify(text) if text.strip() else ""
        check = readability(text)

        user_id = principal.user_id
        manifest = await self._load(user_id)
        documents = self._documents(manifest)
        previous = documents.get(slot, {})
        archived = [_map(item) for item in cast("list[object]", previous.get("archived") or [])]
        if previous.get("key"):
            archived.insert(
                0,
                {
                    "key": previous.get("key"),
                    "text_key": previous.get("text_key"),
                    "filename": previous.get("filename"),
                    "uploaded_at": previous.get("uploaded_at"),
                },
            )
        for dropped in archived[MAX_ARCHIVED:]:
            await self._delete_objects(dropped)
        archived = archived[:MAX_ARCHIVED]

        object_id = self._ids.new_id().hex
        key = f"{self._prefix(user_id)}/{slot}/{object_id}.pdf"
        text_key = f"{self._prefix(user_id)}/{slot}/{object_id}.txt"
        await self._storage.put(key, raw, "application/pdf")
        await self._storage.put(text_key, safe_text.encode("utf-8"), "text/plain; charset=utf-8")
        entry: dict[str, object] = {
            "key": key,
            "text_key": text_key,
            "filename": sanitise_filename(filename),
            "uploaded_at": self._clock.now().isoformat(),
            "pages": content.pages,
            "size_bytes": len(raw),
            "readability": {
                "sections": check.sections,
                "roles": check.roles,
                "bullets": check.bullets,
                "ok": check.ok,
            },
            "archived": archived,
        }
        documents[slot] = entry
        manifest["documents"] = documents
        await self._save(user_id, manifest)
        return _info(slot, entry)

    async def download(self, principal: Principal, slot: str) -> StoredFile:
        check_slot(slot)
        entry = self._documents(await self._load(principal.user_id)).get(slot, {})
        key = _str(entry.get("key"))
        if not key:
            raise NotFound("No document in that slot.")
        return StoredFile(filename=_str(entry.get("filename")), data=await self._storage.get(key))

    async def delete(self, principal: Principal, slot: str) -> None:
        check_slot(slot)
        user_id = principal.user_id
        manifest = await self._load(user_id)
        documents = self._documents(manifest)
        entry = documents.get(slot, {})
        if not entry.get("key"):
            raise NotFound("No document in that slot.")
        await self._delete_objects(entry)
        documents[slot] = {"archived": entry.get("archived") or []}
        manifest["documents"] = documents
        await self._save(user_id, manifest)

    async def _delete_objects(self, entry: JsonMap) -> None:
        for name in ("key", "text_key"):
            key = _str(entry.get(name))
            if key:
                await self._storage.delete(key)

    async def texts(self, user_id: uuid.UUID) -> dict[str, str]:
        """Guarded, de-identified text per uploaded slot, trimmed for prompts."""
        found: dict[str, str] = {}
        for slot, entry in self._documents(await self._load(user_id)).items():
            text_key = _str(entry.get("text_key"))
            if not text_key:
                continue
            try:
                text = (await self._storage.get(text_key)).decode("utf-8", errors="replace")
            except NotFound:
                continue
            if text.strip():
                found[slot] = text.strip()[:PROMPT_TEXT_CHARS]
        return found

    # -- the LinkedIn connections import summary (kept beside the documents)

    async def record_import(self, user_id: uuid.UUID, summary: ImportSummary) -> None:
        manifest = await self._load(user_id)
        manifest["linkedin_import"] = {
            "filename": summary.filename,
            "imported_at": summary.imported_at.isoformat(),
            "imported": summary.imported,
            "updated": summary.updated,
            "skipped": summary.skipped,
        }
        await self._save(user_id, manifest)

    async def last_import(self, user_id: uuid.UUID) -> ImportSummary | None:
        raw = _map((await self._load(user_id)).get("linkedin_import"))
        at = _str(raw.get("imported_at"))
        if not at:
            return None
        return ImportSummary(
            filename=_str(raw.get("filename")),
            imported_at=datetime.fromisoformat(at),
            imported=_int(raw.get("imported")),
            updated=_int(raw.get("updated")),
            skipped=_int(raw.get("skipped")),
        )
