"""Acknowledged browser routes for messaging channels.

Every route returns ``202 {request_id, event_types}``; channel data reaches the
browser as ``channels.snapshot`` / ``channel.tested`` on ``/api/ui/stream``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Request

import src.backend.http.contracts as http_contracts
from src.backend.channels.base import ChannelError
from src.backend.channels.manager import ChannelManager
from src.backend.channels.store import load_channels, update_channel
from src.backend.events.contracts import UiEvent
from src.backend.http.requests import BroadcastUiEvent, request_correlation_id
from src.backend.storage.documents import BackendDocumentStore


def create_channels_router(
    get_manager: Callable[[], ChannelManager],
    get_store: Callable[[], BackendDocumentStore],
    broadcast: BroadcastUiEvent,
) -> APIRouter:
    router = APIRouter()

    async def _stored_document(channel_id: str) -> dict[str, Any] | None:
        try:
            stored = await asyncio.to_thread(load_channels, get_store())
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Channels are unavailable") from exc
        if channel_id not in stored:
            raise HTTPException(status_code=404, detail=f"Channel {channel_id!r} not found")
        return stored[channel_id].document

    async def _publish_snapshot(request_id: str) -> None:
        try:
            await get_manager().broadcast_snapshot(request_id=request_id)
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Channels are unavailable") from exc

    @router.get(
        "/channels", status_code=202, response_model=http_contracts.UiRequestAcknowledgement
    )
    async def list_channels(request: Request) -> http_contracts.UiRequestAcknowledgement:
        request_id = request_correlation_id(request, request.headers.get("x-operation-id"))
        await _publish_snapshot(request_id)
        return http_contracts.acknowledgement_response(request_id, [UiEvent.CHANNELS_SNAPSHOT])

    @router.patch(
        "/channels/{channel_id}",
        status_code=202,
        response_model=http_contracts.UiRequestAcknowledgement,
    )
    async def patch_channel(
        channel_id: str, request: Request, body: Any = Body(...)
    ) -> http_contracts.UiRequestAcknowledgement:
        request_id = request_correlation_id(request, request.headers.get("x-operation-id"))
        try:
            await asyncio.to_thread(update_channel, get_store(), channel_id, body)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc).strip("'\"")) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Channels are unavailable") from exc
        # The store watcher also reconciles; reconciling is idempotent, and
        # doing it here keeps the channel live without a Postgres listener.
        get_manager().request_reconnect(channel_id)
        await _publish_snapshot(request_id)
        return http_contracts.acknowledgement_response(request_id, [UiEvent.CHANNELS_SNAPSHOT])

    @router.post(
        "/channels/{channel_id}/test",
        status_code=202,
        response_model=http_contracts.UiRequestAcknowledgement,
    )
    async def test_channel(
        channel_id: str, request: Request
    ) -> http_contracts.UiRequestAcknowledgement:
        request_id = request_correlation_id(request, request.headers.get("x-operation-id"))
        document = await _stored_document(channel_id)
        payload: dict[str, Any] = {"channel_id": channel_id, "ok": True}
        try:
            await get_manager().send_test(channel_id, document)
        except ChannelError as exc:
            payload = {"channel_id": channel_id, "ok": False, "error": str(exc)}
        broadcast(UiEvent.CHANNEL_TESTED, payload, request_id=request_id)
        return http_contracts.acknowledgement_response(request_id, [UiEvent.CHANNEL_TESTED])

    @router.post(
        "/channels/{channel_id}/reconnect",
        status_code=202,
        response_model=http_contracts.UiRequestAcknowledgement,
    )
    async def reconnect_channel(
        channel_id: str, request: Request
    ) -> http_contracts.UiRequestAcknowledgement:
        request_id = request_correlation_id(request, request.headers.get("x-operation-id"))
        document = await _stored_document(channel_id)
        if document is not None and not document["enabled"]:
            raise HTTPException(status_code=409, detail="Channel is turned off")
        get_manager().request_reconnect(channel_id, force=True)
        await _publish_snapshot(request_id)
        return http_contracts.acknowledgement_response(request_id, [UiEvent.CHANNELS_SNAPSHOT])

    return router
