"""GCP guard adapters: Model Armor (ContentGuard) and Sensitive Data Protection (PII).

Both fail closed: any transport error, non-200 status or unexpected body raises
`UpstreamUnavailable`, which callers treat as a block. Only exercised against respx mocks.
"""

from __future__ import annotations

from typing import cast

import httpx

from app.adapters.http import AccessTokenSource
from app.domain.errors import UpstreamUnavailable
from app.domain.ports import GuardAction, GuardVerdict, PiiSpan

type Json = dict[str, object]


async def _post(http: httpx.AsyncClient, tokens: AccessTokenSource, url: str, body: Json) -> Json:
    try:
        token = await tokens.token()
        response = await http.post(url, json=body, headers={"Authorization": f"Bearer {token}"})
    except httpx.HTTPError:
        raise UpstreamUnavailable("A safety service is unavailable.") from None
    if not response.is_success:
        raise UpstreamUnavailable("A safety service is unavailable.")
    try:
        payload: object = response.json()
    except ValueError:
        raise UpstreamUnavailable("A safety service returned an unreadable answer.") from None
    if not isinstance(payload, dict):
        raise UpstreamUnavailable("A safety service returned an unreadable answer.")
    return cast("Json", payload)


def model_armor_host(location: str) -> str:
    return f"modelarmor.{location}.rep.googleapis.com"


class ModelArmorGuard:
    def __init__(
        self,
        http: httpx.AsyncClient,
        tokens: AccessTokenSource,
        *,
        project: str,
        location: str,
        template: str,
    ) -> None:
        self._http = http
        self._tokens = tokens
        self._base = (
            f"https://{model_armor_host(location)}/v1/projects/{project}"
            f"/locations/{location}/templates/{template}"
        )

    async def inspect_input(self, text: str) -> GuardVerdict:
        body: Json = {"userPromptData": {"text": text}}
        payload = await _post(self._http, self._tokens, f"{self._base}:sanitizeUserPrompt", body)
        return _armor_verdict(payload)

    async def inspect_output(self, text: str) -> GuardVerdict:
        body: Json = {"modelResponseData": {"text": text}}
        url = f"{self._base}:sanitizeModelResponse"
        return _armor_verdict(await _post(self._http, self._tokens, url, body))


def _armor_verdict(payload: Json) -> GuardVerdict:
    result = payload.get("sanitizationResult")
    if not isinstance(result, dict):
        raise UpstreamUnavailable("A safety service returned an unreadable answer.")
    result = cast("Json", result)
    if result.get("invocationResult", "SUCCESS") != "SUCCESS":
        raise UpstreamUnavailable("A safety service could not complete the check.")
    state = result.get("filterMatchState")
    if state == "NO_MATCH_FOUND":
        return GuardVerdict(action=GuardAction.ALLOWED)
    if state != "MATCH_FOUND":
        raise UpstreamUnavailable("A safety service returned an unreadable answer.")
    filters = result.get("filterResults")
    categories = _matched_filters(cast("Json", filters)) if isinstance(filters, dict) else ()
    return GuardVerdict(action=GuardAction.BLOCK, categories=categories)


def _matched_filters(filters: Json) -> tuple[str, ...]:
    return tuple(sorted(name for name, value in filters.items() if _has_match(value)))


def _has_match(node: object) -> bool:
    if isinstance(node, dict):
        mapping = cast("Json", node)
        if mapping.get("matchState") == "MATCH_FOUND":
            return True
        return any(_has_match(child) for child in mapping.values())
    if isinstance(node, list):
        return any(_has_match(child) for child in cast("list[object]", node))
    return False


class SdpPII:
    """De-identification uses templates (deterministic crypto transform, KMS-wrapped key)."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        tokens: AccessTokenSource,
        *,
        project: str,
        location: str,
        deidentify_template: str,
        reidentify_template: str,
        inspect_template: str,
    ) -> None:
        self._http = http
        self._tokens = tokens
        self._base = f"https://dlp.googleapis.com/v2/projects/{project}/locations/{location}"
        self._deidentify_template = deidentify_template
        self._reidentify_template = reidentify_template
        self._inspect_template = inspect_template

    async def deidentify(self, text: str) -> str:
        body: Json = {
            "item": {"value": text},
            "deidentifyTemplateName": self._deidentify_template,
            "inspectTemplateName": self._inspect_template,
        }
        return _item_value(await self._call("content:deidentify", body))

    async def reidentify(self, text: str) -> str:
        body: Json = {
            "item": {"value": text},
            "reidentifyTemplateName": self._reidentify_template,
            "inspectTemplateName": self._inspect_template,
        }
        return _item_value(await self._call("content:reidentify", body))

    async def find(self, text: str) -> list[PiiSpan]:
        body: Json = {"item": {"value": text}, "inspectTemplateName": self._inspect_template}
        payload = await self._call("content:inspect", body)
        result = payload.get("result")
        findings: object = (
            cast("Json", result).get("findings", []) if isinstance(result, dict) else []
        )
        if not isinstance(findings, list):
            raise UpstreamUnavailable("A safety service returned an unreadable answer.")
        return sorted(
            (_span(cast("Json", item)) for item in cast("list[object]", findings)),
            key=lambda span: (span.start, span.end),
        )

    async def _call(self, method: str, body: Json) -> Json:
        return await _post(self._http, self._tokens, f"{self._base}/{method}", body)


def _item_value(payload: Json) -> str:
    item = payload.get("item")
    value = cast("Json", item).get("value") if isinstance(item, dict) else None
    if not isinstance(value, str):
        raise UpstreamUnavailable("A safety service returned an unreadable answer.")
    return value


def _span(finding: Json) -> PiiSpan:
    try:
        location = cast("Json", finding["location"])
        codepoints = cast("Json", location["codepointRange"])
        info_type = cast("Json", finding.get("infoType", {}))
        # int64 fields arrive as JSON strings; a missing start means 0 (proto3 default).
        start = int(cast("str", codepoints.get("start", 0)))
        end = int(cast("str", codepoints["end"]))
    except (KeyError, TypeError, ValueError):
        raise UpstreamUnavailable("A safety service returned an unreadable answer.") from None
    return PiiSpan(start=start, end=end, info_type=str(info_type.get("name", "UNKNOWN")))
