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

import src.backend.http.contracts as http_contracts
from src.backend.http.requests import BroadcastUiEvent, request_correlation_id
from src.backend.storage.documents import PROGENA_STATE_PREFIX, BackendDocumentStore
from src.progena.settings.model_profiles import (
    MODEL_PROFILES_DOC_KEY,
    default_model_profiles_payload,
    migrate_legacy_model_profiles_payload,
    validate_model_profiles_payload,
)
from src.progena.settings.providers import (
    GO_RESPONSES_MODELS,
    ProviderRepository,
    get_chatgpt_models,
)


def _safe_public_profile(name: str, profile: dict[str, Any]) -> dict[str, Any]:
    """Expose supported configuration only, never arbitrary private fields."""
    provider_id = profile.get("provider_id")
    if not isinstance(provider_id, str) or not provider_id:
        raise HTTPException(status_code=422, detail=f"Profile {name!r}: invalid provider_id")
    public = {
        "name": name,
        "provider_id": provider_id,
        "deployment": profile["deployment"],
    }
    if "context_window" in profile:
        context_window = profile["context_window"]
        if type(context_window) is not int or context_window <= 0:
            raise HTTPException(status_code=422, detail=f"Profile {name!r}: invalid context_window")
        public["context_window"] = context_window
    if "cache_ttl_seconds" in profile:
        cache_ttl_seconds = profile["cache_ttl_seconds"]
        if type(cache_ttl_seconds) is not int or cache_ttl_seconds <= 0:
            raise HTTPException(
                status_code=422, detail=f"Profile {name!r}: invalid cache_ttl_seconds"
            )
        public["cache_ttl_seconds"] = cache_ttl_seconds
    if "pricing" in profile:
        public["pricing"] = dict(profile["pricing"])
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
    document = store.get_document(PROGENA_STATE_PREFIX, MODEL_PROFILES_DOC_KEY)
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

    store.mutate_document(PROGENA_STATE_PREFIX, MODEL_PROFILES_DOC_KEY, mutator)


def _migrate_legacy_profiles(
    store: BackendDocumentStore, repository: ProviderRepository | None
) -> None:
    """Persist legacy Azure profile references once a default Foundry exists."""
    if repository is None:
        return
    document = store.get_document(PROGENA_STATE_PREFIX, MODEL_PROFILES_DOC_KEY)
    payload = document.payload if document is not None else None
    if not isinstance(payload, dict) or not any(
        isinstance(profile, dict) and profile.get("provider") == "azure_openai"
        for profile in payload.values()
    ):
        return
    foundry_id = next(
        (
            str(provider["id"])
            for provider in repository.list_public()
            if provider["kind"] == "foundry" and provider["is_default"]
        ),
        None,
    )
    if foundry_id is None:
        return

    def migrate(current: Any) -> Any:
        return migrate_legacy_model_profiles_payload(current, foundry_id)

    store.mutate_document(PROGENA_STATE_PREFIX, MODEL_PROFILES_DOC_KEY, migrate)


def create_model_profile_router(
    get_store: Callable[[], BackendDocumentStore],
    broadcast: BroadcastUiEvent,
    get_provider_repository: Callable[[], ProviderRepository] | None = None,
) -> APIRouter:
    router = APIRouter()

    @router.get(
        "/model-profiles", status_code=202, response_model=http_contracts.UiRequestAcknowledgement
    )
    async def list_model_profiles(request: Request) -> http_contracts.UiRequestAcknowledgement:
        request_id = request_correlation_id(request, request.headers.get("x-operation-id"))
        store = get_store()
        repository: ProviderRepository | None = None
        if get_provider_repository is not None:
            try:
                repository = get_provider_repository()
            except RuntimeError:
                repository = None
        await asyncio.to_thread(_migrate_legacy_profiles, store, repository)
        await broadcast_model_profiles_snapshot(store, broadcast, request_id=request_id)
        return http_contracts.acknowledgement_response(request_id, ["profiles.snapshot"])

    @router.patch(
        "/model-profiles/{name:path}",
        status_code=202,
        response_model=http_contracts.UiRequestAcknowledgement,
    )
    async def patch_model_profile(
        name: str, body: dict[str, Any], request: Request
    ) -> http_contracts.UiRequestAcknowledgement:
        request_id = request_correlation_id(request, request.headers.get("x-operation-id"))

        allowed_patch_keys = {
            "provider_id",
            "deployment",
            "pricing",
            "context_window",
            "cache_ttl_seconds",
            "reasoning_enabled",
            "reasoning_effort",
            "reasoning_summary",
        }
        extra_keys = set(body.keys()) - allowed_patch_keys
        if extra_keys:
            raise HTTPException(
                status_code=422, detail=f"Unsupported fields in patch body: {sorted(extra_keys)}"
            )
        patch_body = dict(body)
        for field in ("provider_id", "deployment"):
            if field not in patch_body:
                continue
            value = patch_body[field]
            if not isinstance(value, str) or not value.strip():
                raise HTTPException(status_code=422, detail=f"{field} must be a non-empty string")
            patch_body[field] = value.strip()
        validated_stored_provider_id: str | None = None

        repository: ProviderRepository | None = None
        if get_provider_repository is not None:
            try:
                repository = get_provider_repository()
            except RuntimeError as exc:
                raise HTTPException(
                    status_code=503, detail="Provider settings are unavailable"
                ) from exc

        def patch(profile: dict[str, Any]) -> dict[str, Any] | None:
            if (
                validated_stored_provider_id is not None
                and profile["provider_id"] != validated_stored_provider_id
            ):
                raise HTTPException(
                    status_code=409,
                    detail="Profile provider changed during validation; retry the request",
                )
            if "provider_id" in patch_body:
                profile["provider_id"] = patch_body["provider_id"]
            if "deployment" in patch_body:
                profile["deployment"] = patch_body["deployment"]
            if "pricing" in patch_body:
                if patch_body["pricing"] is None:
                    profile.pop("pricing", None)
                else:
                    profile["pricing"] = patch_body["pricing"]
            if "context_window" in patch_body:
                context_window = patch_body["context_window"]
                if context_window is None:
                    profile.pop("context_window", None)
                elif type(context_window) is not int or context_window <= 0:
                    raise HTTPException(
                        status_code=422, detail="context_window must be a positive integer"
                    )
                else:
                    profile["context_window"] = context_window
            if "cache_ttl_seconds" in patch_body:
                cache_ttl_seconds = patch_body["cache_ttl_seconds"]
                if cache_ttl_seconds is None:
                    profile.pop("cache_ttl_seconds", None)
                elif type(cache_ttl_seconds) is not int or cache_ttl_seconds <= 0:
                    raise HTTPException(
                        status_code=422, detail="cache_ttl_seconds must be a positive integer"
                    )
                else:
                    profile["cache_ttl_seconds"] = cache_ttl_seconds
            for field in ("reasoning_enabled", "reasoning_effort", "reasoning_summary"):
                if field not in patch_body:
                    continue
                value = patch_body[field]
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
            if repository is not None:
                provider_id = profile.get("provider_id")
                provider = next(
                    (item for item in repository.list_public() if item["id"] == provider_id),
                    None,
                )
                if provider is None:
                    raise HTTPException(
                        status_code=422, detail="provider_id does not reference a provider"
                    )
                if (
                    provider["kind"] == "opencode_go"
                    and profile["deployment"] not in GO_RESPONSES_MODELS
                ):
                    raise HTTPException(
                        status_code=422,
                        detail="deployment is not supported by OpenCode Go",
                    )
            return profile

        store = get_store()
        await asyncio.to_thread(_migrate_legacy_profiles, store, repository)
        if repository is not None and ("provider_id" in patch_body or "deployment" in patch_body):
            current_profiles = list(
                (await asyncio.to_thread(_build_profiles_snapshot, store))["profiles"]
            )
            stored_profile = next(
                (item for item in current_profiles if item["name"] == name),
                None,
            )
            if stored_profile is not None:
                validated_stored_provider_id = stored_profile["provider_id"]
                provider_id = patch_body.get("provider_id", validated_stored_provider_id)
                deployment = patch_body.get("deployment", stored_profile["deployment"])
                provider = next(
                    (
                        item
                        for item in await asyncio.to_thread(repository.list_public)
                        if item["id"] == provider_id
                    ),
                    None,
                )
                if provider is not None and provider["kind"] == "chatgpt":
                    try:
                        chatgpt_models = await get_chatgpt_models(str(provider["id"]))
                    except (LookupError, ValueError, ConnectionError, TimeoutError) as exc:
                        raise HTTPException(
                            status_code=503, detail="ChatGPT model catalog is unavailable"
                        ) from exc
                    model_slugs = {model["slug"] for model in chatgpt_models}
                    if deployment not in model_slugs:
                        raise HTTPException(
                            status_code=422,
                            detail="deployment is not available for the selected ChatGPT account",
                        )
        await asyncio.to_thread(_mutate_profile, store, name, patch)
        await broadcast_model_profiles_snapshot(store, broadcast, request_id=request_id)
        return http_contracts.acknowledgement_response(request_id, ["profiles.snapshot"])

    @router.delete(
        "/model-profiles/{name:path}",
        status_code=202,
        response_model=http_contracts.UiRequestAcknowledgement,
    )
    async def delete_model_profile(
        name: str, request: Request
    ) -> http_contracts.UiRequestAcknowledgement:
        request_id = request_correlation_id(request, request.headers.get("x-operation-id"))
        repository: ProviderRepository | None = None
        if get_provider_repository is not None:
            try:
                repository = get_provider_repository()
            except RuntimeError:
                repository = None
        store = get_store()
        await asyncio.to_thread(_migrate_legacy_profiles, store, repository)
        await asyncio.to_thread(_mutate_profile, store, name, lambda _profile: None)
        await broadcast_model_profiles_snapshot(store, broadcast, request_id=request_id)
        return http_contracts.acknowledgement_response(request_id, ["profiles.snapshot"])

    return router
