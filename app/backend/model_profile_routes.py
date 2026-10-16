"""Model profile routes — the backend-persisted ``/config/profiles.json`` document.

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


def _public_profiles(raw: Any) -> dict[str, Any]:
    """The validated public snapshot of one stored catalog (HTTP 422 if invalid)."""

    try:
        profiles = validate_model_profiles_payload(raw)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"profiles": [_safe_public_profile(name, profile) for name, profile in profiles.items()]}


def _build_profiles_snapshot(store: BackendDocumentStore) -> dict[str, Any]:
    document = store.get_document(PROGENITOR_STATE_PREFIX, MODEL_PROFILES_DOC_KEY)
    return _public_profiles(
        document.payload if document is not None else default_model_profiles_payload()
    )


async def broadcast_model_profiles_snapshot(
    store: BackendDocumentStore,
    broadcast: BroadcastUiEvent,
    *,
    request_id: str | None = None,
) -> None:
    payload = await asyncio.to_thread(_build_profiles_snapshot, store)
    # asyncio subscriber queues and replay bookkeeping belong to the event loop.
    broadcast("profiles.snapshot", payload, request_id=request_id)


def _mutate_profile(
    store: BackendDocumentStore,
    name: str,
    change: Callable[[dict[str, Any]], dict[str, Any] | None],
) -> None:
    """Apply ``change`` to one profile under the document's lock.

    ``change`` returns the replacement profile, or ``None`` to delete it. The
    read, change and write are one transaction, so concurrent edits of any
    profiles serialize instead of overwriting each other.
    """

    def mutator(payload: Any) -> Any:
        raw = payload if payload is not None else default_model_profiles_payload()
        if not isinstance(raw, dict):
            raise HTTPException(status_code=422, detail="Stored profile document is malformed")
        try:
            profiles = validate_model_profiles_payload(dict(raw))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if name not in profiles:
            raise HTTPException(status_code=404, detail=f"Profile {name!r} not found")
        # Names are unique after whitespace normalization (the validator rejects
        # ambiguity), so exactly one raw key holds this profile; keep its form.
        stored_name = next(key for key in raw if key.strip() == name)
        replacement = change(dict(profiles[name]))
        updated = {key: value for key, value in raw.items() if key != stored_name}
        if replacement is not None:
            updated[stored_name] = replacement
        # The acknowledged snapshot must be publishable before the write commits.
        _public_profiles(updated)
        return updated

    store.mutate_document(PROGENITOR_STATE_PREFIX, MODEL_PROFILES_DOC_KEY, mutator)


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

        def patch(profile: dict[str, Any]) -> dict[str, Any] | None:
            if "deployment" in body:
                deployment = body["deployment"]
                if not isinstance(deployment, str) or not deployment.strip():
                    raise HTTPException(
                        status_code=422, detail="deployment must be a non-empty string"
                    )
                profile["deployment"] = deployment.strip()
            for field in ("reasoning_enabled", "reasoning_effort", "reasoning_summary"):
                if field not in body:
                    continue
                value = body[field]
                if value is None:
                    profile.pop(field, None)
                elif field == "reasoning_enabled":
                    if not isinstance(value, bool):
                        raise HTTPException(status_code=422, detail=f"{field} must be a boolean")
                    profile[field] = value
                else:
                    if not isinstance(value, str) or not value.strip():
                        raise HTTPException(
                            status_code=422, detail=f"{field} must be a non-empty string"
                        )
                    profile[field] = value.strip()
            return profile

        store = get_store()
        await asyncio.to_thread(_mutate_profile, store, name, patch)
        await broadcast_model_profiles_snapshot(store, broadcast, request_id=request_id)
        return acknowledgement_response(request_id, ["profiles.snapshot"])

    @router.delete(
        "/model-profiles/{name:path}", status_code=202, response_model=UiRequestAcknowledgement
    )
    async def delete_model_profile(name: str, request: Request) -> UiRequestAcknowledgement:
        request_id = request_correlation_id(request, request.headers.get("x-request-id"))
        store = get_store()
        await asyncio.to_thread(_mutate_profile, store, name, lambda _profile: None)
        await broadcast_model_profiles_snapshot(store, broadcast, request_id=request_id)
        return acknowledgement_response(request_id, ["profiles.snapshot"])

    return router
