"""Gateway routes for the runtime-owned per-conversation chat queue."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field, model_validator

import src.backend.events.contracts as events_contracts
import src.backend.events.stream as events_stream
import src.backend.lifecycle.state as lifecycle_state
from src.backend.http.contracts import UiRequestAcknowledgement, acknowledgement_response
from src.backend.http.requests import BroadcastUiEvent, request_correlation_id
from src.backend.integrations.runtime import RuntimeQueueError


class ChatQueueActionRequest(BaseModel):
    """One optimistic-concurrency queue mutation owned by the runtime."""

    action: Literal["delete", "reorder", "send_now"]
    message_id: str | None = None
    order: list[str] | None = None
    revision: int = Field(ge=0)
    command_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_action_target(self) -> ChatQueueActionRequest:
        if self.action in {"delete", "send_now"} and not self.message_id:
            raise ValueError(f"message_id is required for {self.action}")
        if self.action == "reorder" and self.order is None:
            raise ValueError("order is required for reorder")
        return self


def create_chat_queue_router(
    *,
    get_runtime_client: Callable[[], Any] = lambda: lifecycle_state.client,
    broadcast_ui_event: BroadcastUiEvent = events_stream.broadcast_ui_event,
) -> APIRouter:
    """Create the non-persistent browser adapter for runtime queue operations."""
    router = APIRouter(prefix="/api/chat/sessions", tags=["chat"])

    def runtime_client() -> Any:
        client = get_runtime_client()
        if client is None:
            raise HTTPException(status_code=503, detail="Chat queue is unavailable")
        return client

    def publish_snapshot(snapshot: dict[str, Any], request_id: str) -> None:
        conversation_id = snapshot["conversation_id"]
        payload = dict(snapshot)
        payload["request_id"] = request_id
        broadcast_ui_event(
            events_contracts.UiEvent.CHAT_QUEUE_UPDATED,
            payload,
            scope="chat",
            session_id=conversation_id,
            request_id=request_id,
        )

    async def invoke(operation: Callable[..., Any], *args: Any, request_id: str) -> None:
        try:
            snapshot = await operation(*args)
        except RuntimeQueueError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Chat queue is unavailable") from exc
        publish_snapshot(snapshot, request_id)

    @router.get(
        "/{conversation_id}/queue",
        status_code=202,
        response_model=UiRequestAcknowledgement,
    )
    async def get_queue(
        conversation_id: str,
        request: Request,
        x_request_id: str | None = Header(default=None, alias="X-Operation-Id"),
    ) -> UiRequestAcknowledgement:
        request_id = request_correlation_id(request, x_request_id)
        client = runtime_client()
        await invoke(client.get_chat_queue, conversation_id, request_id=request_id)
        return acknowledgement_response(request_id, [events_contracts.UiEvent.CHAT_QUEUE_UPDATED])

    @router.post(
        "/{conversation_id}/queue/actions",
        status_code=202,
        response_model=UiRequestAcknowledgement,
    )
    async def apply_queue_action(
        conversation_id: str,
        body: ChatQueueActionRequest,
        request: Request,
        x_request_id: str | None = Header(default=None, alias="X-Operation-Id"),
    ) -> UiRequestAcknowledgement:
        request_id = request_correlation_id(request, x_request_id)
        client = runtime_client()
        await invoke(
            client.apply_chat_queue_action,
            conversation_id,
            body.model_dump(exclude_none=True),
            request_id=request_id,
        )
        return acknowledgement_response(request_id, [events_contracts.UiEvent.CHAT_QUEUE_UPDATED])

    return router
