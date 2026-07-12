"""Convert native DeepAgent/LangGraph streams into IPC stream items."""

from __future__ import annotations

import inspect
import logging
import time
from collections.abc import AsyncIterator
from typing import Any, cast
from urllib.parse import urlsplit, urlunsplit

import httpx
from langchain_core.messages import AIMessageChunk, HumanMessage

from ..runtime.messages import (
    attach_reasoning_trace,
    coerce_messages_list,
    extract_display_metadata,
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

# Nested (subgraph-namespaced) custom event_type -> the subagent_-prefixed
# type surfaced to the UI. Any inner custom event not in this map (e.g. a
# subagent's own debug_event) is dropped in v1 rather than surfaced generically.
_NESTED_TOOL_EVENT_REMAP = {
    "tool_start": "subagent_tool_start",
    "tool_result": "subagent_tool_result",
    "tool_error": "subagent_tool_error",
}


def _extract_tool_call_id_from_task_input(payload: Any) -> str | None:
    """Pull a triggering tool_call_id out of a ``tasks``-mode start payload's ``input``.

    LangGraph schedules each parallel tool call as its own Pregel push task whose
    ``input`` is a ``tool_call_with_context`` dict (current shape) or a list of
    tool-call dicts (legacy shape) -- mirrors langchain's own
    ``_TasksLifecycleBase._record_pending_tool_calls``.
    """
    if isinstance(payload, dict):
        tool_call = payload.get("tool_call")
        if isinstance(tool_call, dict):
            candidate = tool_call.get("id")
            if isinstance(candidate, str):
                return candidate
        return None
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                candidate = item.get("id")
                if isinstance(candidate, str):
                    return candidate
    return None


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
        self._streamed_reasoning_content = ""
        self._response_was_streamed = False
        self._reasoning_was_streamed = False
        self._task_subagent_calls: dict[str, str] = {}
        # Pregel push-task id -> triggering tool_call_id, harvested from
        # stream_mode="tasks" start events at the outer namespace. A nested
        # subgraph's own namespace segment is "<node_name>:<task_id>", so this
        # is a causally-exact join, unlike namespace arrival/first-seen order --
        # empirically confirmed unreliable (~50% mismatch) under genuine
        # concurrent async subagent dispatch, since ToolNode races parallel
        # tool calls via asyncio.gather.
        self._pending_dispatch_task_ids: dict[str, str] = {}
        # namespace -> (tool_call_id, subagent_name) bindings resolved from the
        # join above. Scoped to one DeepAgentIPCStream instance, i.e. one chat
        # turn -- no cross-turn leakage, so no eviction is needed.
        self._subagent_namespace_bindings: dict[tuple[Any, ...], tuple[str, str]] = {}
        self._last_emitted_assistant_metadata: dict[str, Any] | None = None

    async def stream(
        self,
        message: str,
        *,
        thread_id: str,
        received_at: float | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield IPC stream items from ``graph.astream``."""

        human_message = HumanMessage(
            content=message,
            additional_kwargs={
                "created_at": received_at if received_at is not None else time.time()
            },
        )
        graph_input: dict[str, Any] = {"messages": [human_message]}
        self.state["current_input"] = message
        self.state["cycle_count"] = int(self.state.get("cycle_count") or 0) + 1
        astream_kwargs: dict[str, Any] = {
            "config": {"configurable": {"thread_id": thread_id}},
        }
        if self._graph_accepts_kwarg("stream_mode"):
            astream_kwargs["stream_mode"] = ["messages", "updates", "custom", "tasks"]
        if self._graph_accepts_kwarg("subgraphs"):
            astream_kwargs["subgraphs"] = True

        async for stream_item in self.graph.astream(graph_input, **astream_kwargs):
            namespace, mode, event = self._unpack_stream_item(stream_item)

            if mode == "tasks":
                self._record_pending_dispatch_task_id(namespace, event)
                continue

            if namespace:
                async for item in self._handle_namespaced_stream_item(namespace, mode, event):
                    yield item
                continue

            if mode == "messages":
                async for item in self._handle_message_stream_event(event):
                    yield item
                continue

            if mode == "custom":
                for custom_item in self._handle_custom_stream_event(event):
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
            self._reasoning_was_streamed = True
            self._streamed_reasoning_content += reasoning_delta
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

    def _handle_custom_stream_event(self, event: Any) -> list[dict[str, Any]]:
        envelope = normalize_custom_event(event)
        if envelope is None:
            return []
        item: dict[str, Any] = {
            "type": "stream_event",
            "event_type": envelope["event_type"],
            "content": envelope["content"],
            "metadata": envelope["metadata"],
        }
        # Images are kept at the top level of the payload to bypass the 2 KB
        # metadata truncation in normalize_custom_event.
        if isinstance(event, dict) and envelope["event_type"] == "tool_result":
            images = event.get("images")
            if isinstance(images, list) and images:
                item["images"] = images
        items = [item]
        items.extend(self._expand_task_subagent_events(envelope))
        return items

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

            if "todos" in node_output:
                yield {
                    "type": "stream_event",
                    "event_type": "todos_update",
                    "content": "",
                    "metadata": {"todos": node_output["todos"]},
                }

            display_metadata: dict[str, Any] = {}
            if "messages" in node_output:
                last_msg = get_last_ai_message(node_output.get("messages"))
                if last_msg is not None:
                    display_metadata = extract_display_metadata(last_msg)

            # Emit assistant_metadata once per changed payload, before the
            # node_update event so the frontend can label the assistant row
            # before any text chunk or tool/debug event arrives. Some direct
            # A2A replies do not carry a stable AI message id, so payload
            # change — not message identity — is the safe dedupe key here.
            if display_metadata and display_metadata != self._last_emitted_assistant_metadata:
                self._last_emitted_assistant_metadata = dict(display_metadata)
                yield {
                    "type": "stream_event",
                    "event_type": "assistant_metadata",
                    "content": "",
                    "metadata": dict(display_metadata),
                }

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
        display_metadata = extract_display_metadata(last_ai_msg) if last_ai_msg is not None else {}
        response_text = self.last_response
        if response_text is None:
            response_text = extract_message_text(last_ai_msg)
            if response_text:
                if display_metadata and display_metadata != self._last_emitted_assistant_metadata:
                    self._last_emitted_assistant_metadata = dict(display_metadata)
                    yield {
                        "type": "stream_event",
                        "event_type": "assistant_metadata",
                        "content": "",
                        "metadata": dict(display_metadata),
                    }
                async for item in self._ensure_response_streamed(response_text):
                    yield item
                self.last_response = response_text
                self.state["last_cycle_response"] = response_text

        reasoning_tokens = (
            extract_reasoning_token_count(last_ai_msg) if last_ai_msg is not None else None
        )
        reasoning_trace = extract_reasoning_trace(last_ai_msg) if last_ai_msg is not None else None
        if not reasoning_trace and self._streamed_reasoning_content:
            reasoning_trace = self._streamed_reasoning_content
        if (
            last_ai_msg is not None
            and not reasoning_trace
            and not self._reasoning_was_streamed
            and reasoning_tokens
        ):
            reasoning_trace = await self._retrieve_reasoning_summary(last_ai_msg)
        if last_ai_msg is not None and reasoning_trace:
            attach_reasoning_trace(
                last_ai_msg,
                reasoning_trace,
                reasoning_tokens=reasoning_tokens,
            )
        self.last_reasoning_trace = reasoning_trace
        self.state["last_reasoning_trace"] = reasoning_trace
        if last_ai_msg is not None and bool(reasoning_trace) and not self._reasoning_was_streamed:
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
        elif last_ai_msg is not None and self._reasoning_was_streamed:
            summary_event = build_reasoning_summary_event(
                reasoning_trace or self._streamed_reasoning_content,
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

    async def _retrieve_reasoning_summary(self, msg: Any) -> str | None:
        response_metadata = getattr(msg, "response_metadata", None)
        response_id = response_metadata.get("id") if isinstance(response_metadata, dict) else None
        llm_config = getattr(self.config, "llm", None)
        base_url = getattr(llm_config, "base_url", "") if llm_config is not None else ""
        api_key = getattr(llm_config, "api_key", "") if llm_config is not None else ""
        if (
            not isinstance(response_id, str)
            or not response_id
            or not isinstance(base_url, str)
            or not base_url
        ):
            return None
        if not isinstance(api_key, str) or not api_key:
            return None

        parsed = urlsplit(base_url.strip())
        path = (parsed.path or "").rstrip("/")
        base_path = path[: -len("/responses")] if path.endswith("/responses") else path
        endpoint = urlunsplit(
            (
                parsed.scheme,
                parsed.netloc,
                f"{base_path}/responses/{response_id}",
                parsed.query,
                parsed.fragment,
            )
        )

        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(30.0),
                headers={"api-key": api_key},
            ) as client:
                response = await client.get(endpoint)
                response.raise_for_status()
                data = response.json()
        except Exception:
            logger.debug(
                "Failed to retrieve reasoning summary for response %s", response_id, exc_info=True
            )
            return None

        summaries: list[str] = []
        for item in data.get("output", []):
            if not isinstance(item, dict) or item.get("type") != "reasoning":
                continue
            summary_items = item.get("summary")
            if not isinstance(summary_items, list):
                continue
            for part in summary_items:
                if not isinstance(part, dict):
                    continue
                text = part.get("text") or part.get("content")
                if isinstance(text, str) and text.strip():
                    summaries.append(text.strip())
        return "\n\n".join(summaries) if summaries else None

    def _expand_task_subagent_events(self, envelope: dict[str, Any]) -> list[dict[str, Any]]:
        metadata = envelope.get("metadata")
        if not isinstance(metadata, dict):
            return []

        tool_name = str(metadata.get("tool_name") or envelope.get("content") or "")
        if tool_name != "task":
            return []

        tool_call_id = str(metadata.get("tool_call_id") or "")
        event_type = str(envelope.get("event_type") or "")

        if event_type == "tool_start":
            args_preview = metadata.get("args_preview")
            subagent_name = ""
            if isinstance(args_preview, dict):
                raw_name = args_preview.get("subagent_type")
                if isinstance(raw_name, str):
                    subagent_name = raw_name
            if not subagent_name:
                return []
            if tool_call_id:
                self._task_subagent_calls[tool_call_id] = subagent_name
            return [
                {
                    "type": "stream_event",
                    "event_type": "subagent_start",
                    "content": subagent_name,
                    "metadata": {
                        "subagent_name": subagent_name,
                        "tool_call_id": tool_call_id,
                    },
                }
            ]

        subagent_name = self._task_subagent_calls.pop(tool_call_id, "")
        if not subagent_name:
            return []

        if event_type == "tool_result":
            return [
                {
                    "type": "stream_event",
                    "event_type": "subagent_result",
                    "content": subagent_name,
                    "metadata": {
                        "subagent_name": subagent_name,
                        "tool_call_id": tool_call_id,
                        "success": bool(metadata.get("success", True)),
                        "duration_ms": metadata.get("duration_ms"),
                        "result_preview": metadata.get("result_preview"),
                    },
                }
            ]

        if event_type == "tool_error":
            return [
                {
                    "type": "stream_event",
                    "event_type": "subagent_result",
                    "content": subagent_name,
                    "metadata": {
                        "subagent_name": subagent_name,
                        "tool_call_id": tool_call_id,
                        "success": False,
                        "duration_ms": metadata.get("duration_ms"),
                        "error": metadata.get("error"),
                    },
                }
            ]

        return []

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

    def _graph_accepts_kwarg(self, name: str) -> bool:
        astream = getattr(self.graph, "astream", None)
        if not callable(astream):
            return False
        try:
            parameters = inspect.signature(astream).parameters.values()
            return any(
                parameter.name == name or parameter.kind == inspect.Parameter.VAR_KEYWORD
                for parameter in parameters
            )
        except (TypeError, ValueError):
            return False

    def _unpack_stream_item(self, stream_item: Any) -> tuple[tuple[Any, ...], str, Any]:
        """Normalize one graph.astream() item to (namespace, mode, event).

        LangGraph yields 2-tuples ``(mode, event)`` normally, and 3-tuples
        ``(namespace, mode, event)`` once ``subgraphs=True`` is set -- without
        this normalization a 3-tuple would silently fall through the old
        2-tuple unpack (mode becomes the raw tuple, the isinstance(event, dict)
        guard drops it), dropping every event, not just the new ones.
        """
        if isinstance(stream_item, tuple) and len(stream_item) == 3:
            return stream_item[0], stream_item[1], stream_item[2]
        if isinstance(stream_item, tuple) and len(stream_item) == 2:
            return (), stream_item[0], stream_item[1]
        return (), "updates", stream_item

    def _record_pending_dispatch_task_id(self, namespace: tuple[Any, ...], event: Any) -> None:
        """Harvest task_id -> tool_call_id from an outer-level tasks-mode start event.

        Only start events (no "result" key) at the outer namespace matter: a
        nested subgraph's own namespace segment is "<node_name>:<task_id>",
        where <task_id> is exactly this Pregel push task's own id, letting
        _resolve_subagent_binding join back to the exact triggering tool call.
        """
        if namespace or not isinstance(event, dict) or "result" in event:
            return
        task_id = event.get("id")
        if not isinstance(task_id, str) or not task_id:
            return
        tool_call_id = _extract_tool_call_id_from_task_input(event.get("input"))
        if tool_call_id:
            self._pending_dispatch_task_ids[task_id] = tool_call_id

    def _resolve_subagent_binding(self, namespace: tuple[Any, ...]) -> tuple[str, str] | None:
        """Bind a subgraph namespace to the (tool_call_id, subagent_name) that spawned it.

        Namespaces are opaque LangGraph-internal ids, not the task tool_call_id --
        joined via the task_id embedded in the namespace's own last segment
        ("<node_name>:<task_id>") against tasks-mode dispatch bookkeeping. This is
        causally exact (harvested from the dispatching task's own input), unlike
        namespace first-seen order, which does not reliably track dispatch order
        under genuine concurrent async subagent execution.
        """
        bound = self._subagent_namespace_bindings.get(namespace)
        if bound is not None:
            return bound

        _, _, task_id = str(namespace[-1]).partition(":")
        if not task_id:
            return None
        tool_call_id = self._pending_dispatch_task_ids.get(task_id)
        if not tool_call_id:
            return None
        subagent_name = self._task_subagent_calls.get(tool_call_id, "")
        if not subagent_name:
            return None

        binding = (tool_call_id, subagent_name)
        self._subagent_namespace_bindings[namespace] = binding
        return binding

    async def _handle_namespaced_stream_item(
        self, namespace: tuple[Any, ...], mode: str, event: Any
    ) -> AsyncIterator[dict[str, Any]]:
        binding = self._resolve_subagent_binding(namespace)
        if binding is None:
            logger.debug(
                "Dropping namespaced stream item with no pending subagent binding: ns=%r mode=%s",
                namespace,
                mode,
            )
            return

        tool_call_id, subagent_name = binding

        if mode == "messages":
            async for item in self._handle_nested_message_stream_event(
                tool_call_id, subagent_name, event
            ):
                yield item
            return

        if mode == "custom":
            for item in self._handle_nested_custom_stream_event(tool_call_id, subagent_name, event):
                yield item
            return

        # `updates` mode from inside a nested subagent graph is a no-op in v1.

    async def _handle_nested_message_stream_event(
        self, tool_call_id: str, subagent_name: str, event: Any
    ) -> AsyncIterator[dict[str, Any]]:
        if not isinstance(event, tuple) or len(event) != 2:
            logger.debug("Ignoring unexpected nested messages stream payload: %r", event)
            return

        message_chunk, _metadata = event
        if not isinstance(message_chunk, AIMessageChunk):
            return

        chunk_text = extract_stream_chunk_text(message_chunk)
        if not chunk_text:
            return

        yield {
            "type": "stream_event",
            "event_type": "subagent_message_delta",
            "content": chunk_text,
            "metadata": {
                "tool_call_id": tool_call_id,
                "subagent_name": subagent_name,
                "text_length": len(chunk_text),
            },
        }

    def _handle_nested_custom_stream_event(
        self, tool_call_id: str, subagent_name: str, event: Any
    ) -> list[dict[str, Any]]:
        envelope = normalize_custom_event(event)
        if envelope is None:
            return []

        inner_event_type = envelope["event_type"]
        remapped_type = _NESTED_TOOL_EVENT_REMAP.get(inner_event_type)
        if remapped_type is None:
            return []

        inner_metadata = envelope.get("metadata")
        inner_metadata = dict(inner_metadata) if isinstance(inner_metadata, dict) else {}
        nested_tool_call_id = inner_metadata.pop("tool_call_id", None)
        nested_tool_name = inner_metadata.pop("tool_name", None)
        inner_metadata["tool_call_id"] = tool_call_id
        inner_metadata["subagent_name"] = subagent_name
        inner_metadata["nested_tool_call_id"] = nested_tool_call_id
        inner_metadata["nested_tool_name"] = nested_tool_name

        item: dict[str, Any] = {
            "type": "stream_event",
            "event_type": remapped_type,
            "content": envelope["content"],
            "metadata": inner_metadata,
        }
        # Images are kept at the top level of the payload to bypass the 2 KB
        # metadata truncation in normalize_custom_event, mirroring
        # _handle_custom_stream_event's top-level tool_result handling.
        if isinstance(event, dict) and inner_event_type == "tool_result":
            images = event.get("images")
            if isinstance(images, list) and images:
                item["images"] = images
        return [item]
