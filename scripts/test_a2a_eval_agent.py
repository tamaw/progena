#!/usr/bin/env python3
"""Minimal A2A agent used by live evaluation scripts."""

from __future__ import annotations

import asyncio
import os
import uuid
from contextlib import asynccontextmanager
from typing import Any

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.apps import A2AFastAPIApplication
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentSkill,
    Message,
    Part,
    Role,
    Task,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
    TextPart,
)
from fastapi.responses import JSONResponse

AGENT_NAME = os.getenv("TEST_A2A_AGENT_NAME", "live_eval_agent").strip() or "live_eval_agent"
HOST = os.getenv("TEST_A2A_AGENT_HOST", "127.0.0.1").strip() or "127.0.0.1"
PORT = int(os.getenv("TEST_A2A_AGENT_PORT", "9810"))
COMPLETE_DELAY_SECONDS = float(os.getenv("TEST_A2A_AGENT_COMPLETE_DELAY", "1.0"))


def _utc_now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _text_message(text: str) -> Message:
    return Message(
        message_id=str(uuid.uuid4()),
        role=Role.agent,
        parts=[Part(root=TextPart(text=text))],
    )


def _request_text(message: Message | None) -> str:
    if message is None:
        return ""
    parts = getattr(message, "parts", None) or []
    return "".join(
        getattr(getattr(part, "root", part), "text", "")
        for part in parts
        if isinstance(getattr(getattr(part, "root", part), "text", None), str)
    ).strip()


class EvalExecutor(AgentExecutor):
    """Accept a task immediately, then complete it shortly afterwards."""

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task_id = context.task_id or str(uuid.uuid4())
        context_id = context.context_id or str(uuid.uuid4())
        request_text = _request_text(context.message) or "No request provided."

        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=task_id,
                context_id=context_id,
                final=False,
                status=TaskStatus(
                    state=TaskState.submitted,
                    message=_text_message(f"Accepted: {request_text}"),
                    timestamp=_utc_now_iso(),
                ),
            )
        )

        asyncio.create_task(
            self._complete_later(
                event_queue.tap(),
                task_id=task_id,
                context_id=context_id,
                request_text=request_text,
            )
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=context.task_id or "",
                context_id=context.context_id or "",
                final=True,
                status=TaskStatus(
                    state=TaskState.canceled,
                    message=_text_message("Cancelled."),
                    timestamp=_utc_now_iso(),
                ),
            )
        )

    async def _complete_later(
        self,
        event_queue: EventQueue,
        *,
        task_id: str,
        context_id: str,
        request_text: str,
    ) -> None:
        await asyncio.sleep(COMPLETE_DELAY_SECONDS)
        await TASK_STORE.save(
            Task(
                id=task_id,
                contextId=context_id,
                history=[],
                status=TaskStatus(
                    state=TaskState.completed,
                    message=_text_message(f"Completed: {request_text}"),
                    timestamp=_utc_now_iso(),
                ),
            )
        )
        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=task_id,
                context_id=context_id,
                final=True,
                status=TaskStatus(
                    state=TaskState.completed,
                    message=_text_message(f"Completed: {request_text}"),
                    timestamp=_utc_now_iso(),
                ),
            )
        )


AGENT_CARD = AgentCard(
    name=AGENT_NAME,
    description="Temporary A2A test agent for Progenitor live evaluations.",
    version="0.1.0",
    url=f"http://{HOST}:{PORT}",
    capabilities=AgentCapabilities(streaming=False, state_transition_history=True),
    default_input_modes=["text/plain"],
    default_output_modes=["text/plain"],
    skills=[
        AgentSkill(
            id="echo_async_completion",
            name="Echo async completion",
            description="Accepts a task, then completes it with the original request text.",
            tags=["test", "evaluation", "a2a"],
            examples=["Echo async completion for token TEST_TOKEN_123"],
        )
    ],
)

TASK_STORE = InMemoryTaskStore()
_handler = DefaultRequestHandler(agent_executor=EvalExecutor(), task_store=TASK_STORE)
_app = A2AFastAPIApplication(agent_card=AGENT_CARD, http_handler=_handler)
app = _app.build()


async def _health(_request: Any) -> JSONResponse:
    return JSONResponse(
        content={
            "status": "healthy",
            "name": AGENT_NAME,
            "timestamp": _utc_now_iso(),
        }
    )


app.add_route("/health", _health, methods=["GET"])


@asynccontextmanager
async def _lifespan(_app: Any):
    yield


app.router.lifespan_context = _lifespan


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
