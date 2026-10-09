"""Execution policies use the shared acknowledged-request / SSE document flow."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.progenitor.policies import POLICY_KEY_PREFIX, validate_policy

from .chat_sessions import acknowledgement_response
from .document_store import PROGENITOR_STATE_PREFIX, BackendDocumentStore
from .request_utils import BroadcastUiEvent, request_correlation_id


def policy_catalog(store: BackendDocumentStore) -> list[dict[str, Any]]:
    return [
        document.payload
        for _, document in store.list_documents(PROGENITOR_STATE_PREFIX, POLICY_KEY_PREFIX)
    ]


def create_policy_router(
    get_store: Callable[[], BackendDocumentStore], broadcast: BroadcastUiEvent
) -> APIRouter:
    router = APIRouter()

    @router.get("/policies", status_code=202)
    async def list_policies(request: Request):
        request_id = request_correlation_id(request, request.headers.get("x-request-id"))
        items = await asyncio.to_thread(policy_catalog, get_store())
        broadcast("policies.snapshot", {"items": items}, request_id=request_id)
        return acknowledgement_response(request_id, ["policies.snapshot"])

    @router.put("/policies/{policy_id}", status_code=202)
    async def save_policy(policy_id: str, request: Request):
        try:
            policy = validate_policy(await request.json())
            if policy.id != policy_id:
                raise ValueError("Policy id must match URL")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        store = get_store()
        await asyncio.to_thread(
            store.save_document,
            PROGENITOR_STATE_PREFIX,
            f"{POLICY_KEY_PREFIX}{policy_id}",
            policy.model_dump(mode="json"),
        )
        request_id = request_correlation_id(request, request.headers.get("x-request-id"))
        broadcast(
            "policies.snapshot",
            {"items": await asyncio.to_thread(policy_catalog, store)},
            request_id=request_id,
        )
        return acknowledgement_response(request_id, ["policies.snapshot"])

    return router
