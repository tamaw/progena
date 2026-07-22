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
        self.event_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(
            maxsize=runtime.max_output_queue_size
        )
        self.a2a_completion_queue: asyncio.Queue[A2ATaskRecord] = asyncio.Queue(
            maxsize=runtime.max_output_queue_size
        )
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
            "event_queue": {
                "size": self.event_queue.qsize(),
                "maxsize": self.event_queue.maxsize,
                "full": self.event_queue.full(),
            },
        }
