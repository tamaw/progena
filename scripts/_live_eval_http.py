"""Shared HTTP + SSE helpers for the live evaluation scripts.

Both ``evaluate_progenitor_live.py`` and ``evaluate_heartbeat_live.py`` talk to
the Progenitor runtime's persistent HTTP server (chat over ``POST /v1/responses``,
background events over ``GET /v1/events``) instead of the old TCP IPC socket.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx

DEFAULT_BASE_URL = "http://127.0.0.1:9501"


async def iter_sse_frames(
    response: httpx.Response,
) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    """Parse ``event:``/``data:`` SSE frames from a streaming httpx response."""
    event_name: str | None = None
    async for line in response.aiter_lines():
        if line == "" or line.startswith(":"):
            continue
        if line.startswith("event: "):
            event_name = line[len("event: ") :]
            continue
        if line.startswith("data: "):
            if event_name is None:
                continue
            yield event_name, json.loads(line[len("data: ") :])
            event_name = None


# One conversation per evaluation run: multi-turn scenarios (recall, follow-ups)
# need their turns on the same thread, and a request without a conversation
# would get a fresh isolated one from the runtime.
LIVE_EVAL_CONVERSATION = f"conv_live_eval_{uuid.uuid4().hex[:12]}"


async def http_stream_items(
    client: httpx.AsyncClient,
    timeout: float,
    prompt: str,
    *,
    metadata: dict[str, Any] | None = None,
    request_id_prefix: str = "live-eval-stream",
) -> list[dict[str, Any]]:
    """Send a chat message and collect the raw streamed item dicts."""
    items: list[dict[str, Any]] = []
    body = {
        "input": prompt,
        "stream": True,
        "conversation": LIVE_EVAL_CONVERSATION,
        "metadata": {
            "request_id": f"{request_id_prefix}-{uuid.uuid4().hex[:8]}",
            **(metadata or {"source": "live_evaluation"}),
        },
    }
    async with client.stream("POST", "/v1/responses", json=body, timeout=timeout) as response:
        if response.status_code != 200:
            error_body = await response.aread()
            raise RuntimeError(f"responses request failed ({response.status_code}): {error_body!r}")
        async for event_name, data in iter_sse_frames(response):
            if event_name == "response.queued":
                continue
            if event_name == "response.failed":
                error = data.get("response", {}).get("error", {})
                raise RuntimeError(str(error.get("message") or "unknown chat error"))
            if event_name in ("response.completed", "response.incomplete"):
                break
            items.append(data)
    return items


async def http_assistant_response(
    client: httpx.AsyncClient,
    timeout: float,
    prompt: str,
    *,
    metadata: dict[str, Any] | None = None,
    request_id_prefix: str = "live-eval",
) -> str:
    """Send a chat message and return the concatenated assistant text."""
    items = await http_stream_items(
        client, timeout, prompt, metadata=metadata, request_id_prefix=request_id_prefix
    )
    text_parts: list[str] = []
    for item in items:
        if item.get("type") == "response.output_text.delta":
            text_parts.append(str(item.get("delta") or ""))
        elif item.get("type") == "stream_chunk":
            # Backward-compat for any legacy bridges still emitting stream_chunk
            text_parts.append(str(item.get("content") or ""))
    return "".join(text_parts)


class EventCollector:
    """Collects ``/v1/events`` broadcast frames in the background.

    Open this *before* triggering the action under test so a fast completion
    callback can't race the subscriber into existence.
    """

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client
        self.events: list[dict[str, Any]] = []
        self._task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> EventCollector:
        self._task = asyncio.create_task(self._run())
        await asyncio.sleep(0.2)  # let the SSE connection establish
        return self

    async def __aexit__(self, *_exc_info: Any) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, TimeoutError):
                await asyncio.wait_for(self._task, timeout=5.0)

    async def _run(self) -> None:
        try:
            async with self._client.stream("GET", "/v1/events") as response:
                async for event_name, data in iter_sse_frames(response):
                    if event_name == "agent_event":
                        self.events.append(data)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass

    async def wait_for(
        self, predicate: Callable[[dict[str, Any]], bool], *, deadline: float
    ) -> dict[str, Any] | None:
        """Poll already-collected events for one matching ``predicate`` until ``deadline``."""
        while True:
            for event in self.events:
                if predicate(event):
                    return event
            if time.time() > deadline:
                return None
            await asyncio.sleep(0.5)

    async def wait_for_types(
        self, event_types: set[str], token: str, *, deadline: float
    ) -> list[dict[str, Any]]:
        """Poll for events whose type is in ``event_types`` and mention ``token``.

        Keeps polling until every requested type has been observed at least
        once, or ``deadline`` passes. Returns whatever matched.
        """
        while True:
            matched = [
                event
                for event in self.events
                if str(event.get("event_type") or "") in event_types
                and (
                    token in str(event.get("content") or "")
                    or token in json.dumps(event.get("metadata") or {}, default=str)
                )
            ]
            if event_types <= {str(event.get("event_type") or "") for event in matched}:
                return matched
            if time.time() > deadline:
                return matched
            await asyncio.sleep(0.5)
