"""Inject a claimed queued user message at a safe root-model boundary."""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, AgentState, hook_config
from langchain_core.messages import BaseMessage, HumanMessage, ToolMessage
from langgraph.config import get_config

from src.progena.runtime.chat_queue import ChatQueueStore


class QueueSteeringMiddleware(AgentMiddleware):
    """Claim durable queued input at safe root-model boundaries.

    A HumanMessage is never placed between an assistant tool request and its
    results. It can be added after the tools node, or after a final assistant
    message where the native loop is explicitly sent back to the model.
    """

    def __init__(self, queue: ChatQueueStore) -> None:
        self._queue = queue

    @staticmethod
    def _thread_id(runtime: Any) -> str | None:
        del runtime
        configurable = get_config().get("configurable")
        thread_id = configurable.get("thread_id") if isinstance(configurable, dict) else None
        return thread_id if isinstance(thread_id, str) and thread_id else None

    @staticmethod
    def _committed_ids(messages: list[Any]) -> set[str]:
        return {
            message.id
            for message in messages
            if isinstance(message, BaseMessage) and isinstance(message.id, str) and message.id
        }

    async def _claim_message(self, state: AgentState[Any], runtime: Any) -> HumanMessage | None:
        thread_id = self._thread_id(runtime)
        messages = list(state.get("messages") or [])
        if thread_id is None or not messages:
            return None
        await self._queue.recover(thread_id, self._committed_ids(messages))
        claimed = await self._queue.claim_next(thread_id, immediate_only=True)
        if claimed is None:
            return None
        additional_kwargs = {
            "created_at": claimed.received_at,
            "attachments": claimed.attachments,
            "metadata": claimed.metadata,
            "progena_queue_send_now": claimed.send_now,
            "progena_queue_request_id": claimed.response_id,
        }
        if claimed.source is not None:
            additional_kwargs["source"] = claimed.source
        return HumanMessage(
            id=claimed.id,
            content=claimed.text,
            additional_kwargs=additional_kwargs,
        )

    async def abefore_model(self, state: AgentState[Any], runtime: Any) -> dict[str, Any] | None:
        """Steer the root's tool continuation after all tool results arrive."""
        messages = list(state.get("messages") or [])
        if not messages or not isinstance(messages[-1], ToolMessage):
            return None
        message = await self._claim_message(state, runtime)
        if message is None:
            return None
        self._emit_claimed(runtime, message)
        return {"messages": [message]}

    @hook_config(can_jump_to=["model"])
    async def aafter_model(self, state: AgentState[Any], runtime: Any) -> dict[str, Any] | None:
        """Drain input that arrives while a final answer is streaming."""
        messages = list(state.get("messages") or [])
        if not messages:
            return None
        last = messages[-1]
        if not isinstance(last, BaseMessage) or bool(getattr(last, "tool_calls", None)):
            return None
        message = await self._claim_message(state, runtime)
        if message is None:
            return None
        self._emit_claimed(runtime, message, boundary=True)
        return {"messages": [message], "jump_to": "model"}

    @staticmethod
    def _emit_claimed(runtime: Any, message: HumanMessage, *, boundary: bool = False) -> None:
        stream_writer = getattr(runtime, "stream_writer", None)
        if not callable(stream_writer):
            return
        metadata = {
            "message_id": message.id,
            "request_id": message.additional_kwargs["progena_queue_request_id"],
            "text": message.content,
            "attachments": message.additional_kwargs["attachments"],
        }
        stream_writer(
            {
                "event_type": "chat_queue_boundary" if boundary else "chat_queue_claimed",
                "content": "",
                "metadata": metadata,
            }
        )
