"""Streaming event routing for Progenitor runtime graph cycles."""

import asyncio
import logging
import time
from typing import Any, cast

from langchain_core.messages import AIMessageChunk

from ..stream_events import (
    build_reasoning_delta_event,
    normalize_custom_event,
)
from ..streaming_utils import (
    extract_stream_chunk_text,
    extract_stream_reasoning_deltas,
    make_json_safe,
)

logger = logging.getLogger(__name__)


class StreamEventRouter:
    """Owns stream queue writes and per-response streaming state."""

    def __init__(self, stream_queue: asyncio.Queue) -> None:
        self._stream_queue = stream_queue
        self.enabled = False
        self.response_was_streamed = False
        self.final_sent = False
        self.streamed_response_content = ""

    def reset(self, *, enabled: bool) -> None:
        self.enabled = enabled
        self.response_was_streamed = False
        self.final_sent = False
        self.streamed_response_content = ""

    async def stream_response(self, text: str) -> None:
        """Stream a response text directly through the stream queue."""
        if not text:
            return
        await self.send_stream_chunk(text, is_final=True)
        logger.debug("Sent complete response (%d chars)", len(text))

    async def stream_token(self, token: str) -> None:
        """Stream a single token directly to the client."""
        if token:
            self.response_was_streamed = True
            self.streamed_response_content += token
            if self.enabled:
                await self.send_stream_chunk(token, is_final=False)

    async def ensure_response_streamed(self, text: str) -> None:
        """Send any missing response text without closing the stream."""
        if not text:
            return
        if self.response_was_streamed:
            if text == self.streamed_response_content:
                return
            if text.startswith(self.streamed_response_content):
                suffix = text[len(self.streamed_response_content) :]
                if suffix:
                    await self.stream_token(suffix)
                return
        await self.stream_token(text)

    async def handle_message_stream_event(self, event: Any) -> None:
        """Handle LangGraph `messages` stream events for token-level output."""
        if not isinstance(event, tuple) or len(event) != 2:
            logger.debug("Ignoring unexpected messages stream payload: %r", event)
            return

        message_chunk, metadata = event
        if not isinstance(message_chunk, AIMessageChunk):
            return

        stream_metadata = make_json_safe(metadata)
        reasoning_deltas = extract_stream_reasoning_deltas(message_chunk)
        if self.enabled:
            for reasoning_delta in reasoning_deltas:
                reasoning_event = build_reasoning_delta_event(
                    reasoning_delta,
                    metadata={
                        "message_chunk_type": type(message_chunk).__name__,
                        "stream_metadata": stream_metadata,
                    },
                )
                await self.send_stream_event(
                    reasoning_event["event_type"],
                    content=reasoning_event["content"],
                    metadata=cast(dict[str, Any], reasoning_event["metadata"]),
                )

        chunk_text = extract_stream_chunk_text(message_chunk)
        if chunk_text:
            await self.stream_token(chunk_text)
        if self.enabled:
            await self.send_stream_event(
                "message_delta",
                content=chunk_text,
                metadata={
                    "message_chunk_type": type(message_chunk).__name__,
                    "stream_metadata": stream_metadata,
                    "text_length": len(chunk_text),
                },
            )

    async def handle_custom_stream_event(self, event: Any) -> None:
        """Forward LangGraph ``custom`` stream events through the IPC channel."""
        if not self.enabled:
            return
        envelope = normalize_custom_event(event)
        if envelope is None:
            return
        await self.send_stream_event(
            envelope["event_type"],
            content=envelope["content"],
            metadata=envelope["metadata"],
        )

    async def stream_final(self) -> None:
        """Mark the end of a streaming response."""
        await self.send_stream_chunk("", is_final=True)

    async def send_stream_event(
        self,
        event_type: str,
        *,
        content: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Send a structured streaming event to the output stream."""
        try:
            self._stream_queue.put_nowait(
                {
                    "type": "stream_event",
                    "event_type": event_type,
                    "content": content,
                    "metadata": make_json_safe(metadata) if metadata is not None else {},
                }
            )
        except asyncio.QueueFull:
            logger.warning(
                "Stream queue full (%d/%d), dropping stream event: %s",
                self._stream_queue.qsize(),
                self._stream_queue.maxsize,
                event_type,
            )

    async def send_stream_chunk(self, chunk: str, is_final: bool = False) -> None:
        """Send a streaming chunk to the output stream."""
        try:
            self._stream_queue.put_nowait(
                {
                    "type": "stream_chunk",
                    "content": chunk,
                    "is_final": is_final,
                }
            )
        except asyncio.QueueFull:
            logger.warning(
                "Stream queue full (%d/%d), dropping chunk",
                self._stream_queue.qsize(),
                self._stream_queue.maxsize,
            )

    async def send_stream_end(self) -> None:
        """Signal the end of a streaming response."""
        try:
            self._stream_queue.put_nowait(
                {
                    "type": "stream_end",
                    "content": "",
                    "is_final": True,
                }
            )
        except asyncio.QueueFull:
            logger.warning("Stream queue full, could not send stream end marker")

    async def get_stream_item(self, timeout: float | None = None) -> dict[str, Any] | None:
        """Get the next streaming queue item, including structured events."""
        try:
            if timeout:
                return cast(
                    dict[str, Any] | None,
                    await asyncio.wait_for(self._stream_queue.get(), timeout=timeout),
                )
            return cast(dict[str, Any] | None, await self._stream_queue.get())
        except TimeoutError:
            return None

    async def get_stream_chunk(self, timeout: float | None = None) -> dict | None:
        """Get the next text streaming chunk from the agent."""
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            remaining = None
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
            item = await self.get_stream_item(timeout=remaining)
            if item is None:
                return None
            if item.get("type") == "stream_chunk":
                return item
