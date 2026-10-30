"""Acknowledged Settings API routes for persisted Responses API providers."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any
from uuid import uuid4

import httpx
from fastapi import APIRouter, HTTPException, Request
from psycopg.errors import UniqueViolation

import src.backend.http.contracts as http_contracts
from src.backend.events import contracts as events_contracts
from src.backend.http.requests import BroadcastUiEvent, request_correlation_id
from src.backend.storage.documents import PROGENA_STATE_PREFIX, BackendDocumentStore
from src.progena.settings.chatgpt_oauth import ChatGPTOAuthError
from src.progena.settings.model_profiles import MODEL_PROFILES_DOC_KEY
from src.progena.settings.providers import (
    GO_RESPONSES_MODELS,
    ProviderRepository,
    get_chatgpt_models,
    test_chatgpt_connection,
    update_chatgpt_provider,
)


def _repository_or_unavailable(
    get_repository: Callable[[], ProviderRepository],
) -> ProviderRepository:
    try:
        return get_repository()
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=503, detail="Provider settings are unavailable") from exc


def _repository_error(exc: Exception) -> HTTPException:
    if isinstance(exc, LookupError):
        return HTTPException(status_code=404, detail="Provider not found")
    if isinstance(exc, UniqueViolation):
        return HTTPException(status_code=409, detail="A provider with that name already exists")
    if isinstance(exc, ValueError):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(status_code=503, detail="Provider settings are unavailable")


async def _snapshot(repository: ProviderRepository) -> dict[str, Any]:
    providers = await asyncio.to_thread(repository.list_public)
    chatgpt_models: dict[str, list[dict[str, str]]] = {}
    chatgpt_model_errors: dict[str, str] = {}
    for provider in providers:
        if provider["kind"] != "chatgpt":
            continue
        provider_id = str(provider["id"])
        try:
            chatgpt_models[provider_id] = await get_chatgpt_models(provider_id)
        except (ChatGPTOAuthError, LookupError, ValueError, ConnectionError, TimeoutError):
            chatgpt_models[provider_id] = []
            chatgpt_model_errors[provider_id] = (
                "Model catalog is unavailable. Reconnect the account and try again."
            )
    return {
        "providers": providers,
        "go_models": list(GO_RESPONSES_MODELS),
        "chatgpt_models": chatgpt_models,
        "chatgpt_model_errors": chatgpt_model_errors,
    }


async def broadcast_provider_snapshot(
    repository: ProviderRepository,
    broadcast: BroadcastUiEvent,
    *,
    request_id: str | None = None,
) -> None:
    payload = await _snapshot(repository)
    broadcast(events_contracts.UiEvent.PROVIDERS_SNAPSHOT, payload, request_id=request_id)


def _profile_references_provider(store: BackendDocumentStore, provider_id: str) -> bool:
    """Return whether a stored profile explicitly points at this connection."""
    document = store.get_document(PROGENA_STATE_PREFIX, MODEL_PROFILES_DOC_KEY)
    payload = document.payload if document is not None else None
    if not isinstance(payload, dict):
        return False
    return any(
        isinstance(profile, dict) and profile.get("provider_id") == provider_id
        for profile in payload.values()
    )


async def _test_connection(
    repository: ProviderRepository,
    provider_id: str,
    provider_kind: str,
    model_default: object = None,
) -> tuple[bool, str | None]:
    if provider_kind == "chatgpt":
        if not isinstance(model_default, str) or not model_default.strip():
            return (False, "Choose a ChatGPT model before testing this connection.")
        try:
            await test_chatgpt_connection(provider_id)
        except (ChatGPTOAuthError, LookupError, ValueError, ConnectionError, TimeoutError):
            return (False, "Connection test failed. Reconnect the ChatGPT account and try again.")
        return (True, None)
    try:
        connection = await asyncio.to_thread(repository.resolve, provider_id)
    except (LookupError, ValueError):
        return (False, "Provider is not configured")
    try:
        headers = {"Content-Type": "application/json"}
        if connection["provider_kind"] == "opencode_go":
            headers["Authorization"] = f"Bearer {connection['api_key']}"
            headers["User-Agent"] = "progena/0.6.0"
            headers["x-opencode-session"] = f"provider-test-{uuid4()}"
        else:
            headers["api-key"] = connection["api_key"]
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{connection['base_url'].rstrip('/')}/responses",
                headers=headers,
                json={
                    "model": connection["model_default"],
                    "input": "ping",
                    "max_output_tokens": 1,
                },
            )
            response.raise_for_status()
    except Exception:  # no upstream response body or credential reaches the Settings stream
        return (False, "Connection test failed")
    return (True, None)


def create_provider_router(
    get_repository: Callable[[], ProviderRepository],
    get_store: Callable[[], BackendDocumentStore],
    broadcast: BroadcastUiEvent,
) -> APIRouter:
    """Create Settings provider endpoints with 202 acknowledgement/SSE semantics."""
    router = APIRouter()

    @router.get(
        "/providers", status_code=202, response_model=http_contracts.UiRequestAcknowledgement
    )
    async def list_providers(request: Request) -> http_contracts.UiRequestAcknowledgement:
        request_id = request_correlation_id(request, request.headers.get("x-operation-id"))
        repository = _repository_or_unavailable(get_repository)
        try:
            await broadcast_provider_snapshot(repository, broadcast, request_id=request_id)
        except Exception as exc:  # database outages should not leak connection details
            raise HTTPException(
                status_code=503, detail="Provider settings are unavailable"
            ) from exc
        return http_contracts.acknowledgement_response(
            request_id, [events_contracts.UiEvent.PROVIDERS_SNAPSHOT]
        )

    @router.post(
        "/providers", status_code=202, response_model=http_contracts.UiRequestAcknowledgement
    )
    async def create_provider(
        body: dict[str, Any], request: Request
    ) -> http_contracts.UiRequestAcknowledgement:
        request_id = request_correlation_id(request, request.headers.get("x-operation-id"))
        repository = _repository_or_unavailable(get_repository)
        try:
            await asyncio.to_thread(repository.create, body)
            await broadcast_provider_snapshot(repository, broadcast, request_id=request_id)
        except Exception as exc:
            raise _repository_error(exc) from exc
        return http_contracts.acknowledgement_response(
            request_id, [events_contracts.UiEvent.PROVIDERS_SNAPSHOT]
        )

    @router.patch(
        "/providers/{provider_id}",
        status_code=202,
        response_model=http_contracts.UiRequestAcknowledgement,
    )
    async def update_provider(
        provider_id: str, body: dict[str, Any], request: Request
    ) -> http_contracts.UiRequestAcknowledgement:
        request_id = request_correlation_id(request, request.headers.get("x-operation-id"))
        repository = _repository_or_unavailable(get_repository)
        try:
            provider = next(
                item
                for item in await asyncio.to_thread(repository.list_public)
                if item["id"] == provider_id
            )
        except StopIteration as exc:
            raise HTTPException(status_code=404, detail="Provider not found") from exc
        try:
            if provider["kind"] == "chatgpt":
                patch = dict(body)
                default_model = patch.pop("default_model", None)
                if "default_model" in body and (
                    not isinstance(default_model, str) or not default_model.strip()
                ):
                    raise ValueError("model must be a non-empty string")
                if patch or default_model is not None:
                    await update_chatgpt_provider(provider_id, patch, default_model)
            else:
                await asyncio.to_thread(repository.update, provider_id, body)
            await broadcast_provider_snapshot(repository, broadcast, request_id=request_id)
        except Exception as exc:
            raise _repository_error(exc) from exc
        return http_contracts.acknowledgement_response(
            request_id, [events_contracts.UiEvent.PROVIDERS_SNAPSHOT]
        )

    @router.delete(
        "/providers/{provider_id}",
        status_code=202,
        response_model=http_contracts.UiRequestAcknowledgement,
    )
    async def delete_provider(
        provider_id: str, request: Request
    ) -> http_contracts.UiRequestAcknowledgement:
        request_id = request_correlation_id(request, request.headers.get("x-operation-id"))
        repository = _repository_or_unavailable(get_repository)
        if await asyncio.to_thread(_profile_references_provider, get_store(), provider_id):
            raise HTTPException(status_code=409, detail="Provider is referenced by a model profile")
        try:
            await asyncio.to_thread(repository.delete, provider_id)
            await broadcast_provider_snapshot(repository, broadcast, request_id=request_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail="Provider not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=503, detail="Provider settings are unavailable"
            ) from exc
        return http_contracts.acknowledgement_response(
            request_id, [events_contracts.UiEvent.PROVIDERS_SNAPSHOT]
        )

    @router.post(
        "/providers/{provider_id}/test",
        status_code=202,
        response_model=http_contracts.UiRequestAcknowledgement,
    )
    async def test_provider(
        provider_id: str, request: Request
    ) -> http_contracts.UiRequestAcknowledgement:
        request_id = request_correlation_id(request, request.headers.get("x-operation-id"))
        repository = _repository_or_unavailable(get_repository)
        try:
            provider = next(
                item
                for item in await asyncio.to_thread(repository.list_public)
                if item["id"] == provider_id
            )
        except StopIteration as exc:
            raise HTTPException(status_code=404, detail="Provider not found") from exc
        ok, error = await _test_connection(
            repository, provider_id, str(provider["kind"]), provider.get("default_model")
        )
        payload: dict[str, Any] = {"provider_id": provider_id, "ok": ok}
        if error is not None:
            payload["error"] = error
        broadcast(events_contracts.UiEvent.PROVIDERS_TESTED, payload, request_id=request_id)
        return http_contracts.acknowledgement_response(
            request_id, [events_contracts.UiEvent.PROVIDERS_TESTED]
        )

    @router.post(
        "/providers/{provider_id}/disconnect",
        status_code=202,
        response_model=http_contracts.UiRequestAcknowledgement,
    )
    async def disconnect_chatgpt_provider(
        provider_id: str, request: Request
    ) -> http_contracts.UiRequestAcknowledgement:
        request_id = request_correlation_id(request, request.headers.get("x-operation-id"))
        repository = _repository_or_unavailable(get_repository)
        try:
            provider = next(
                item
                for item in await asyncio.to_thread(repository.list_public)
                if item["id"] == provider_id
            )
        except StopIteration as exc:
            raise HTTPException(status_code=404, detail="Provider not found") from exc
        if provider["kind"] != "chatgpt":
            raise HTTPException(status_code=422, detail="Provider is not a ChatGPT plan connection")
        try:
            await asyncio.to_thread(repository.disconnect_chatgpt, provider_id)
            await broadcast_provider_snapshot(repository, broadcast, request_id=request_id)
        except Exception as exc:
            raise _repository_error(exc) from exc
        return http_contracts.acknowledgement_response(
            request_id, [events_contracts.UiEvent.PROVIDERS_SNAPSHOT]
        )

    return router
