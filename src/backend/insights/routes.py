"""Acknowledged token-usage snapshot endpoint for the Insights UI."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request

import src.backend.http.contracts as http_contracts
from src.backend.events import contracts as events_contracts
from src.backend.events.stream import require_ui_stream_client
from src.backend.http.requests import BroadcastUiEvent, request_correlation_id
from src.backend.insights.service import build_token_usage_snapshot
from src.backend.storage.documents import BackendDocumentStore


def create_insights_router(
    get_store: Callable[[], BackendDocumentStore], broadcast: BroadcastUiEvent
) -> APIRouter:
    router = APIRouter()

    @router.get(
        "/insights/token-usage",
        status_code=202,
        response_model=http_contracts.UiRequestAcknowledgement,
    )
    async def get_token_usage(
        request: Request,
        range: Literal["24h", "7d", "30d"] = Query(default="24h"),
        profile: str | None = Query(default=None),
        client_id: str = Query(..., min_length=1, max_length=64),
    ) -> http_contracts.UiRequestAcknowledgement:
        """Publish one request-correlated checkpoint-backed usage snapshot."""
        require_ui_stream_client(client_id)
        request_id = request_correlation_id(request, request.headers.get("x-operation-id"))
        try:
            snapshot = await asyncio.to_thread(
                build_token_usage_snapshot,
                get_store(),
                range_name=range,
                profile=profile,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        broadcast(
            events_contracts.UiEvent.INSIGHTS_TOKEN_USAGE_SNAPSHOT,
            snapshot,
            request_id=request_id,
            client_id=client_id,
        )
        return http_contracts.acknowledgement_response(
            request_id, [events_contracts.UiEvent.INSIGHTS_TOKEN_USAGE_SNAPSHOT]
        )

    return router
