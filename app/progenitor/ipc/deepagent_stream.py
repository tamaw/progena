"""Convert native DeepAgent/LangGraph streams into IPC stream items."""

from __future__ import annotations

import inspect
import logging
from collections.abc import AsyncIterator
from typing import Any, cast

from langchain_core.messages import AIMessageChunk, HumanMessage

from ..runtime.messages import (
    coerce_messages_list,
    extract_message_text,
    extract_reasoning_token_count,
    extract_reasoning_trace,
    get_last_ai_message,
    summarize_message_schema,
)
from .stream_events import (
    build_reasoning_delta_event,
    build_reasoning_summary_event,
    normalize_custom_event,
)
from .stream_payloads import (
    extract_stream_chunk_text,
    extract_stream_reasoning_deltas,
    make_json_safe,
)

logger = logging.getLogger(__name__)


class DeepAgentIPCStream:
    """Run a DeepAgent graph stream and expose IPC-friendly items."""

    def __init__(
        self,
        graph: Any,
        *,
        config: Any,
        state: dict[str, Any] | None = None,
    ) -> None:
        self.graph = graph
        self.config = config
        self.state = state if state is not None else {}
        self.last_response: str | None = None
        self.last_reasoning_trace: str | None = None
        self._streamed_response_content = ""
        self._response_was_streamed = False

    async def stream(
        self,
        message: str,
        *,
        thread_id: str,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield IPC stream items from ``graph.astream``."""

        graph_input: dict[str, Any] = {"messages": [HumanMessage(content=message)]}
        self.state["current_input"] = message
        self.state["cycle_count"] = int(self.state.get("cycle_count") or 0) + 1
        astream_kwargs: dict[str, Any] = {
            "config": {"configurable": {"thread_id": thread_id}},
        }
        if self._graph_accepts_stream_mode():
            astream_kwargs["stream_mode"] = ["messages", "updates", "custom"]

        async for stream_item in self.graph.astream(graph_input, **astream_kwargs):
            mode = "updates"
            event = stream_item
            if isinstance(stream_item, tuple) and len(stream_item) == 2:
                mode, event = stream_item

            if mode == "messages":
                async for item in self._handle_message_stream_event(event):
                    yield item
                continue

            if mode == "custom":
                custom_item = self._handle_custom_stream_event(event)
                if custom_item is not None:
                    yield custom_item
                continue

            if mode != "updates" or not isinstance(event, dict):
                logger.debug("Ignoring graph stream item for mode=%s", mode)
                continue

            async for item in self._handle_update_event(event):
                yield item

        async for item in self._finalize_stream():
            yield item

    async def collect_response(self, message: str, *, thread_id: str) -> str:
        """Run the stream and return only assistant text."""

        response = ""
        async for item in self.stream(message, thread_id=thread_id):
            if item.get("type") != "stream_chunk":
                continue
            if item.get("is_final"):
                continue
            response += str(item.get("content", ""))
        return self.last_response or response

    async def _handle_message_stream_event(self, event: Any) -> AsyncIterator[dict[str, Any]]:
        if not isinstance(event, tuple) or len(event) != 2:
            logger.debug("Ignoring unexpected messages stream payload: %r", event)
            return

        message_chunk, metadata = event
        if not isinstance(message_chunk, AIMessageChunk):
            return

        stream_metadata = make_json_safe(metadata)
        for reasoning_delta in extract_stream_reasoning_deltas(message_chunk):
            reasoning_event = build_reasoning_delta_event(
                reasoning_delta,
                metadata={
                    "message_chunk_type": type(message_chunk).__name__,
                    "stream_metadata": stream_metadata,
                },
            )
            yield {
                "type": "stream_event",
                "event_type": reasoning_event["event_type"],
                "content": reasoning_event["content"],
                "metadata": reasoning_event["metadata"],
            }

        chunk_text = extract_stream_chunk_text(message_chunk)
        if chunk_text:
            self._response_was_streamed = True
            self._streamed_response_content += chunk_text
            yield {"type": "stream_chunk", "content": chunk_text, "is_final": False}

        yield {
            "type": "stream_event",
            "event_type": "message_delta",
            "content": chunk_text,
            "metadata": {
                "message_chunk_type": type(message_chunk).__name__,
                "stream_metadata": stream_metadata,
                "text_length": len(chunk_text),
            },
        }

    def _handle_custom_stream_event(self, event: Any) -> dict[str, Any] | None:
        envelope = normalize_custom_event(event)
        if envelope is None:
            return None
        return {
            "type": "stream_event",
            "event_type": envelope["event_type"],
            "content": envelope["content"],
            "metadata": envelope["metadata"],
        }

    async def _handle_update_event(self, event: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        for node_name, node_output in event.items():
            output_info = (
                list(node_output.keys()) if isinstance(node_output, dict) else type(node_output)
            )
            logger.info("GRAPH EVENT: node=%s, output_keys=%s", node_name, output_info)
            if not isinstance(node_output, dict):
                continue

            if "messages" in node_output:
                node_output = {
                    **node_output,
                    "messages": coerce_messages_list(node_output.get("messages")),
                }
            self.state.update(node_output)
            yield {
                "type": "stream_event",
                "event_type": "node_update",
                "content": "",
                "metadata": {
                    "node_name": node_name,
                    "output_keys": output_info,
                    "contains_messages": "messages" in node_output,
                    "contains_pending_response": "pending_response" in node_output,
                },
            }

        pending = self.state.get("pending_response")
        if pending:
            pending_text = str(pending)
            self.last_response = pending_text
            self.state["last_cycle_response"] = pending_text
            async for item in self._ensure_response_streamed(pending_text):
                yield item
            self.state["pending_response"] = None

    async def _finalize_stream(self) -> AsyncIterator[dict[str, Any]]:
        last_ai_msg = get_last_ai_message(self.state.get("messages"))
        response_text = self.last_response
        if response_text is None:
            response_text = extract_message_text(last_ai_msg)
            if response_text:
                async for item in self._ensure_response_streamed(response_text):
                    yield item
                self.last_response = response_text
                self.state["last_cycle_response"] = response_text

        reasoning_trace = extract_reasoning_trace(last_ai_msg) if last_ai_msg is not None else None
        self.last_reasoning_trace = reasoning_trace
        self.state["last_reasoning_trace"] = reasoning_trace
        reasoning_tokens = (
            extract_reasoning_token_count(last_ai_msg) if last_ai_msg is not None else None
        )
        if last_ai_msg is not None and bool(reasoning_trace):
            summary_event = build_reasoning_summary_event(
                reasoning_trace or "",
                metadata={"reasoning_tokens": reasoning_tokens},
            )
            yield {
                "type": "stream_event",
                "event_type": summary_event["event_type"],
                "content": summary_event["content"],
                "metadata": cast(dict[str, Any], summary_event["metadata"]),
            }
        elif last_ai_msg is not None and getattr(
            getattr(self.config, "llm", None), "reasoning_enabled", False
        ):
            logger.info(
                "No reasoning trace found in assistant message schema: %s",
                summarize_message_schema(last_ai_msg),
            )

        yield {"type": "stream_chunk", "content": "", "is_final": True}

    async def _ensure_response_streamed(self, text: str) -> AsyncIterator[dict[str, Any]]:
        if not text:
            return
        if self._response_was_streamed:
            if text == self._streamed_response_content:
                return
            if text.startswith(self._streamed_response_content):
                suffix = text[len(self._streamed_response_content) :]
                if suffix:
                    self._streamed_response_content += suffix
                    yield {"type": "stream_chunk", "content": suffix, "is_final": False}
                return
        self._response_was_streamed = True
        self._streamed_response_content += text
        yield {"type": "stream_chunk", "content": text, "is_final": False}

    def _graph_accepts_stream_mode(self) -> bool:
        astream = getattr(self.graph, "astream", None)
        if not callable(astream):
            return False
        try:
            parameters = inspect.signature(astream).parameters.values()
            return any(
                parameter.name == "stream_mode" or parameter.kind == inspect.Parameter.VAR_KEYWORD
                for parameter in parameters
            )
        except (TypeError, ValueError):
            return False
