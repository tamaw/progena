"""Acknowledged token-usage snapshot endpoint for the Insights UI."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request

from .chat_sessions import UiRequestAcknowledgement, acknowledgement_response
from .document_store import BackendDocumentStore
from .insights import build_token_usage_snapshot
from .request_utils import BroadcastUiEvent, request_correlation_id


def create_insights_router(
    get_store: Callable[[], BackendDocumentStore], broadcast: BroadcastUiEvent
) -> APIRouter:
    router = APIRouter()

    @router.get("/insights/token-usage", status_code=202, response_model=UiRequestAcknowledgement)
    async def get_token_usage(
        request: Request,
        range: Literal["24h", "7d", "30d"] = Query(default="24h"),
        profile: str | None = Query(default=None),
    ) -> UiRequestAcknowledgement:
        """Publish one request-correlated checkpoint-backed usage snapshot."""
        request_id = request_correlation_id(request, request.headers.get("x-request-id"))
        try:
            snapshot = await asyncio.to_thread(
                build_token_usage_snapshot,
                get_store(),
                range_name=range,
                profile=profile,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        broadcast("insights.token_usage.snapshot", snapshot, request_id=request_id)
        return acknowledgement_response(request_id, ["insights.token_usage.snapshot"])

    return router
