"""Model profile routes — the backend-persisted ``/config/profile.json`` document.

Mirrors the acknowledged-request / SSE document flow used by policies and
agent profiles: GET and PATCH return 202 and publish a ``profiles.snapshot``
on the shared ``/api/ui/stream`` so the frontend updates through its realtime
listener rather than the HTTP response body.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.progenitor.state.model_profiles import (
    MODEL_PROFILES_DOC_KEY,
    default_model_profiles_payload,
    validate_model_profiles_payload,
)

from .chat_sessions import UiRequestAcknowledgement, acknowledgement_response
from .document_store import PROGENITOR_STATE_PREFIX, BackendDocumentStore
from .request_utils import BroadcastUiEvent, request_correlation_id


def _safe_public_profile(name: str, profile: dict[str, Any]) -> dict[str, Any]:
    """Expose supported configuration only, never arbitrary private fields."""
    public = {field: profile[field] for field in ("provider", "deployment")}
    public["name"] = name
    for field in ("reasoning_enabled", "reasoning_effort", "reasoning_summary"):
        if field not in profile:
            continue
        value = profile[field]
        valid = (
            isinstance(value, bool)
            if field == "reasoning_enabled"
            else isinstance(value, str) and bool(value.strip())
        )
        if not valid:
            raise HTTPException(status_code=422, detail=f"Profile {name!r}: invalid {field}")
        public[field] = value
    return public


def _build_profiles_snapshot(store: BackendDocumentStore) -> dict[str, Any]:
    document = store.get_document(PROGENITOR_STATE_PREFIX, MODEL_PROFILES_DOC_KEY)
    raw = document.payload if document is not None else default_model_profiles_payload()
    try:
        profiles = validate_model_profiles_payload(raw)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"profiles": [_safe_public_profile(name, profile) for name, profile in profiles.items()]}


async def broadcast_model_profiles_snapshot(
    store: BackendDocumentStore,
    broadcast: BroadcastUiEvent,
    *,
    request_id: str | None = None,
) -> None:
    payload = await asyncio.to_thread(_build_profiles_snapshot, store)
    # asyncio subscriber queues and replay bookkeeping belong to the event loop.
    broadcast("profiles.snapshot", payload, request_id=request_id)


def create_model_profile_router(
    get_store: Callable[[], BackendDocumentStore],
    broadcast: BroadcastUiEvent,
) -> APIRouter:
    router = APIRouter()

    @router.get("/model-profiles", status_code=202, response_model=UiRequestAcknowledgement)
    async def list_model_profiles(request: Request) -> UiRequestAcknowledgement:
        request_id = request_correlation_id(request, request.headers.get("x-request-id"))
        await broadcast_model_profiles_snapshot(get_store(), broadcast, request_id=request_id)
        return acknowledgement_response(request_id, ["profiles.snapshot"])

    @router.patch(
        "/model-profiles/{name:path}", status_code=202, response_model=UiRequestAcknowledgement
    )
    async def patch_model_profile(
        name: str, body: dict[str, Any], request: Request
    ) -> UiRequestAcknowledgement:
        request_id = request_correlation_id(request, request.headers.get("x-request-id"))

        # Reject unknown / arbitrary fields at the top level of the PATCH body.
        allowed_patch_keys = {
            "deployment",
            "reasoning_enabled",
            "reasoning_effort",
            "reasoning_summary",
        }
        extra_keys = set(body.keys()) - allowed_patch_keys
        if extra_keys:
            raise HTTPException(
                status_code=422, detail=f"Unsupported fields in patch body: {sorted(extra_keys)}"
            )

        store = get_store()
        document = await asyncio.to_thread(
            store.get_document, PROGENITOR_STATE_PREFIX, MODEL_PROFILES_DOC_KEY
        )
        default = default_model_profiles_payload()
        raw = document.payload if document else default
        if not isinstance(raw, dict):
            raise HTTPException(status_code=422, detail="Stored profile document is malformed")

        # Validate the existing catalog so we know the profile exists and the
        # store is in a consistent state.
        try:
            profiles = validate_model_profiles_payload(dict(raw))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        if name not in profiles:
            raise HTTPException(status_code=404, detail=f"Profile {name!r} not found")

        profile = dict(profiles[name])

        # Validate and apply dirty fields only.
        if "deployment" in body:
            deployment = body["deployment"]
            if deployment is None or not isinstance(deployment, str) or not deployment.strip():
                raise HTTPException(status_code=422, detail="deployment must be a non-empty string")
            profile["deployment"] = deployment.strip()

        for field in ("reasoning_enabled", "reasoning_effort", "reasoning_summary"):
            if field not in body:
                continue
            value = body[field]
            if value is None:
                profile.pop(field, None)
                continue
            if field == "reasoning_enabled":
                if not isinstance(value, bool):
                    raise HTTPException(status_code=422, detail=f"{field} must be a boolean")
                profile[field] = value
            else:
                if not isinstance(value, str) or not value.strip():
                    raise HTTPException(
                        status_code=422, detail=f"{field} must be a non-empty string"
                    )
                profile[field] = value.strip()

        # Retain raw sibling entries and the original key's identity. The
        # routing validator normalizes surrounding whitespace in public names.
        stored_name = next(key for key in reversed(raw) if key.strip() == name)
        updated_document = {**raw, stored_name: profile}
        public_profiles = {**profiles, name: profile}
        payload = {
            "profiles": [
                _safe_public_profile(profile_name, config)
                for profile_name, config in public_profiles.items()
            ]
        }
        # Validate the complete causal snapshot before the write can commit.
        await asyncio.to_thread(
            store.save_document, PROGENITOR_STATE_PREFIX, MODEL_PROFILES_DOC_KEY, updated_document
        )
        broadcast("profiles.snapshot", payload, request_id=request_id)
        return acknowledgement_response(request_id, ["profiles.snapshot"])

    return router
