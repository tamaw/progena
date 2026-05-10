"""Runtime queue service."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Iterable
from typing import Any, NamedTuple, cast

from ..config import RuntimeConfig
from ..ipc.stream_payloads import make_json_safe
from ..state.models import A2ATaskRecord
from ..utils import utcnow_isoformat

logger = logging.getLogger(__name__)


class QueuedInput(NamedTuple):
    """Input message bundled with its enqueue time."""

    content: Any
    enqueue_time: float


class RuntimeQueues:
    """Owns bounded runtime queues and queue-related primitives."""

    def __init__(self, runtime: RuntimeConfig) -> None:
        self.input_queue: asyncio.Queue[QueuedInput] = asyncio.Queue(
            maxsize=runtime.max_input_queue_size
        )
        self.output_queue: asyncio.Queue[str] = asyncio.Queue(maxsize=runtime.max_output_queue_size)
        self.stream_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(
            maxsize=runtime.max_stream_queue_size
        )
        self.event_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(
            maxsize=runtime.max_output_queue_size
        )
        self.a2a_completion_queue: asyncio.Queue[A2ATaskRecord] = asyncio.Queue(
            maxsize=runtime.max_output_queue_size
        )
        self.cycle_complete_event = asyncio.Event()
        self.current_input_wait_seconds = 0.0
        self.current_input_pending_count = 0

    async def push_event(
        self,
        event_type: str,
        content: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        metadata_safe: dict[str, Any] = {}
        if metadata:
            try:
                metadata_safe = make_json_safe(metadata)
            except Exception:
                metadata_safe = {"raw": str(metadata)}

        event = {
            "event_type": event_type,
            "content": content,
            "metadata": metadata_safe,
            "timestamp": utcnow_isoformat(),
        }
        try:
            self.event_queue.put_nowait(event)
            logger.debug("Pushed event: %s", event_type)
        except asyncio.QueueFull:
            logger.warning(
                "Event queue full (%d/%d), dropping event: %s",
                self.event_queue.qsize(),
                self.event_queue.maxsize,
                event_type,
            )

    async def get_event(self, timeout: float | None = None) -> dict[str, Any] | None:
        try:
            if timeout:
                return cast(
                    dict[str, Any] | None,
                    await asyncio.wait_for(self.event_queue.get(), timeout=timeout),
                )
            return cast(dict[str, Any] | None, await self.event_queue.get())
        except TimeoutError:
            return None

    async def get_input(self, timeout: float) -> Any:
        effective_timeout = min(timeout, 30.0)
        try:
            queued_input = await asyncio.wait_for(self.input_queue.get(), timeout=effective_timeout)
            self.current_input_wait_seconds = time.monotonic() - queued_input.enqueue_time
            self.current_input_pending_count = self.input_queue.qsize()
            return queued_input.content
        except TimeoutError:
            return None

    async def send_output(self, message: str) -> None:
        try:
            self.output_queue.put_nowait(message)
            logger.info("Queued output message for client")
        except asyncio.QueueFull:
            logger.warning(
                "Output queue full (%d/%d), dropping message",
                self.output_queue.qsize(),
                self.output_queue.maxsize,
            )

    async def get_output(self, timeout: float | None = None) -> str | None:
        try:
            if timeout:
                return cast(
                    str | None, await asyncio.wait_for(self.output_queue.get(), timeout=timeout)
                )
            return cast(str | None, await self.output_queue.get())
        except TimeoutError:
            return None

    def signal_cycle_complete(self) -> None:
        self.cycle_complete_event.set()

    def enqueue_a2a_completions(self, records: Iterable[A2ATaskRecord]) -> int:
        queued = 0
        for record in records:
            try:
                self.a2a_completion_queue.put_nowait(record)
                queued += 1
            except asyncio.QueueFull:
                logger.warning(
                    "A2A completion queue full (%d/%d), dropping task completion: %s",
                    self.a2a_completion_queue.qsize(),
                    self.a2a_completion_queue.maxsize,
                    record.task_id,
                )
        return queued

    def drain_a2a_completions(self) -> list[A2ATaskRecord]:
        records: list[A2ATaskRecord] = []
        while True:
            try:
                records.append(self.a2a_completion_queue.get_nowait())
            except asyncio.QueueEmpty:
                return records

    async def wait_cycle_complete(self, timeout: float | None = None) -> bool:
        try:
            if timeout:
                await asyncio.wait_for(self.cycle_complete_event.wait(), timeout=timeout)
            else:
                await self.cycle_complete_event.wait()
            self.cycle_complete_event.clear()
            return True
        except TimeoutError:
            return False

    async def send_stream_event(
        self,
        event_type: str,
        *,
        content: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        try:
            self.stream_queue.put_nowait(
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
                self.stream_queue.qsize(),
                self.stream_queue.maxsize,
                event_type,
            )

    async def send_stream_chunk(self, chunk: str, is_final: bool = False) -> None:
        try:
            self.stream_queue.put_nowait(
                {
                    "type": "stream_chunk",
                    "content": chunk,
                    "is_final": is_final,
                }
            )
        except asyncio.QueueFull:
            logger.warning(
                "Stream queue full (%d/%d), dropping chunk",
                self.stream_queue.qsize(),
                self.stream_queue.maxsize,
            )

    async def send_stream_end(self) -> None:
        try:
            self.stream_queue.put_nowait(
                {
                    "type": "stream_end",
                    "content": "",
                    "is_final": True,
                }
            )
        except asyncio.QueueFull:
            logger.warning("Stream queue full, could not send stream end marker")

    async def get_stream_item(self, timeout: float | None = None) -> dict[str, Any] | None:
        try:
            if timeout:
                return cast(
                    dict[str, Any] | None,
                    await asyncio.wait_for(self.stream_queue.get(), timeout=timeout),
                )
            return cast(dict[str, Any] | None, await self.stream_queue.get())
        except TimeoutError:
            return None

    async def get_stream_chunk(self, timeout: float | None = None) -> dict[str, Any] | None:
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

    async def enqueue_input(
        self,
        message: str,
        timeout: float | None = 30.0,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        payload: Any = {"content": message, "metadata": metadata or {}} if metadata else message
        queued_input = QueuedInput(content=payload, enqueue_time=time.monotonic())
        try:
            if timeout == 0:
                self.input_queue.put_nowait(queued_input)
            elif timeout is None:
                await self.input_queue.put(queued_input)
            else:
                await asyncio.wait_for(self.input_queue.put(queued_input), timeout=timeout)
            return True
        except asyncio.QueueFull:
            logger.warning("Input queue full, message rejected (non-blocking)")
            raise
        except TimeoutError:
            logger.warning(
                "Input queue full, message rejected after %.1fs timeout. Queue size: %d/%d",
                timeout,
                self.input_queue.qsize(),
                self.input_queue.maxsize,
            )
            return False

    def get_queue_stats(self) -> dict[str, Any]:
        return {
            "input_queue": {
                "size": self.input_queue.qsize(),
                "maxsize": self.input_queue.maxsize,
                "full": self.input_queue.full(),
            },
            "output_queue": {
                "size": self.output_queue.qsize(),
                "maxsize": self.output_queue.maxsize,
                "full": self.output_queue.full(),
            },
            "stream_queue": {
                "size": self.stream_queue.qsize(),
                "maxsize": self.stream_queue.maxsize,
                "full": self.stream_queue.full(),
            },
            "event_queue": {
                "size": self.event_queue.qsize(),
                "maxsize": self.event_queue.maxsize,
                "full": self.event_queue.full(),
            },
        }
