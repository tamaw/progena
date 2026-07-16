"""Measure first visible assistant text through direct, runtime, and backend paths.

All measurements inherit the caller's environment. Use the repository virtual
environment when running this script:

    .venv/bin/python scripts/measure_first_token.py direct
    .venv/bin/python scripts/measure_first_token.py runtime
    .venv/bin/python scripts/measure_first_token.py backend

The direct measurement includes both graph construction time and warm graph
request-to-first-token time. Runtime and backend measurements start immediately
before their network request. The first-token boundary deliberately ignores
reasoning/tool events and waits for non-empty visible text.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

PROMPT = "Say exactly READY and nothing else."
DEBUG = os.getenv("MEASURE_DEBUG", "").lower() in {"1", "true", "yes"}


def debug(message: str) -> None:
    if DEBUG:
        print(f"[measure] {message}", file=sys.stderr, flush=True)


DEFAULT_RUNTIME_URL = "http://127.0.0.1:9501"
DEFAULT_BACKEND_URL = "http://127.0.0.1:8000"


async def iter_sse(response: httpx.Response) -> AsyncIterator[tuple[str | None, Any]]:
    """Yield parsed SSE event name and JSON data payload."""
    event_name: str | None = None
    data_lines: list[str] = []
    async for line in response.aiter_lines():
        if line == "":
            if data_lines:
                raw = "\n".join(data_lines)
                try:
                    data: Any = json.loads(raw)
                except json.JSONDecodeError:
                    data = raw
                yield event_name, data
            event_name = None
            data_lines = []
            continue
        if line.startswith(":"):
            continue
        field, separator, value = line.partition(":")
        if not separator:
            continue
        value = value[1:] if value.startswith(" ") else value
        if field == "event":
            event_name = value
        elif field == "data":
            data_lines.append(value)
    if data_lines:
        raw = "\n".join(data_lines)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = raw
        yield event_name, data


def now_ns() -> int:
    return time.perf_counter_ns()


def result(
    path: str,
    started_ns: int,
    first_token_ns: int | None,
    *,
    graph_ready_ns: int | None = None,
    **extra: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "path": path,
        "prompt": PROMPT,
        "first_token_ms": (
            round((first_token_ns - started_ns) / 1_000_000, 3)
            if first_token_ns is not None
            else None
        ),
        "completed": first_token_ns is not None,
    }
    if graph_ready_ns is not None:
        payload["graph_build_ms"] = round((graph_ready_ns - started_ns) / 1_000_000, 3)
        payload["request_to_first_token_ms"] = (
            round((first_token_ns - graph_ready_ns) / 1_000_000, 3)
            if first_token_ns is not None
            else None
        )
    payload.update(extra)
    return payload


async def measure_direct() -> dict[str, Any]:
    """Construct the canonical DeepAgent and measure its native stream."""
    from langgraph.checkpoint.memory import MemorySaver

    from app.progenitor.agent.agent import create_progenitor_agent
    from app.progenitor.config import get_config
    from app.progenitor.ipc.deepagent_stream import DeepAgentIPCStream

    started_ns = now_ns()
    config = get_config(project_root=PROJECT_ROOT, validate=False)
    graph = create_progenitor_agent(
        config=config,
        checkpointer=MemorySaver(),
        project_root=str(PROJECT_ROOT),
    )
    graph_ready_ns = now_ns()
    bridge = DeepAgentIPCStream(graph, config=config, state={})
    thread_id = f"measure-direct-{uuid.uuid4().hex[:12]}"
    first_token_ns: int | None = None
    chunk_count = 0
    response_text = ""
    request_started_ns = now_ns()
    async for item in bridge.stream(PROMPT, thread_id=thread_id, received_at=time.time()):
        if item.get("type") != "stream_chunk" or item.get("is_final"):
            continue
        content = str(item.get("content") or "")
        if not content:
            continue
        chunk_count += 1
        response_text += content
        if first_token_ns is None:
            first_token_ns = now_ns()
    return result(
        "direct_deep_agent",
        request_started_ns,
        first_token_ns,
        chunk_count=chunk_count,
        graph_build_ms=round((graph_ready_ns - started_ns) / 1_000_000, 3),
        process_to_first_token_ms=(
            round((first_token_ns - started_ns) / 1_000_000, 3)
            if first_token_ns is not None
            else None
        ),
        response_preview=response_text[:120],
        thread_id=thread_id,
        model=getattr(config.llm, "model_default", None),
        reasoning_enabled=getattr(config.llm, "reasoning_enabled", None),
        reasoning_effort=getattr(config.llm, "reasoning_effort", None),
    )


async def measure_runtime(base_url: str) -> dict[str, Any]:
    """Measure POST /v1/chat to the runtime's direct SSE response."""
    request_id = f"measure-runtime-{uuid.uuid4().hex[:12]}"
    thread_id = f"measure-runtime-thread-{uuid.uuid4().hex[:12]}"
    payload = {
        "message": PROMPT,
        "request_id": request_id,
        "metadata": {"thread_id": thread_id},
    }
    async with httpx.AsyncClient(timeout=None) as client:
        started_ns = now_ns()
        async with client.stream(
            "POST", f"{base_url.rstrip('/')}/v1/chat", json=payload
        ) as response:
            response.raise_for_status()
            first_token_ns: int | None = None
            chunk_count = 0
            response_text = ""
            async for event_name, data in iter_sse(response):
                if event_name != "stream_chunk" or not isinstance(data, dict):
                    continue
                if data.get("is_final"):
                    continue
                content = str(data.get("content") or "")
                if not content:
                    continue
                chunk_count += 1
                response_text += content
                if first_token_ns is None:
                    first_token_ns = now_ns()
            return result(
                "progenitor_runtime_sse",
                started_ns,
                first_token_ns,
                chunk_count=chunk_count,
                response_preview=response_text[:120],
                request_id=request_id,
                thread_id=thread_id,
                endpoint=f"{base_url.rstrip('/')}/v1/chat",
            )


async def measure_backend(base_url: str) -> dict[str, Any]:
    """Measure backend POST /api/ui/responses to browser-facing UI SSE."""
    request_id = f"measure-backend-{uuid.uuid4().hex[:12]}"
    thread_id = f"measure-backend-thread-{uuid.uuid4().hex[:12]}"
    stream_url = f"{base_url.rstrip('/')}/api/ui/stream"
    response_url = f"{base_url.rstrip('/')}/api/ui/responses"
    body = {
        "model": "progenitor-v1",
        "input": [{"role": "user", "content": PROMPT}],
        "stream": True,
        "metadata": {"thread_id": thread_id},
    }
    async with (
        httpx.AsyncClient(timeout=None) as client,
        client.stream(
            "GET",
            stream_url,
            headers={"Accept": "text/event-stream"},
        ) as stream_response,
    ):
        stream_response.raise_for_status()
        events = iter_sse(stream_response)
        debug("SSE connected; waiting for connection.ready")
        async for event_name, data in events:
            if (
                event_name == "ui.event"
                and isinstance(data, dict)
                and data.get("type") == "connection.ready"
            ):
                debug("received connection.ready")
                break

        started_ns = now_ns()
        acknowledgement = await client.post(
            response_url,
            headers={"X-Request-ID": request_id},
            json=body,
        )
        acknowledgement.raise_for_status()
        acknowledgement_payload = acknowledgement.json()
        effective_request_id = str(acknowledgement_payload.get("request_id") or request_id)
        debug(f"effective request ID: {effective_request_id}")
        acknowledgement_ms = round((now_ns() - started_ns) / 1_000_000, 3)
        first_token_ns: int | None = None
        delta_count = 0
        response_text = ""
        async for event_name, data in events:
            if event_name != "ui.event" or not isinstance(data, dict):
                continue
            if data.get("type") != "chat.response.delta":
                continue
            payload = data.get("payload")
            if not isinstance(payload, dict):
                continue
            if data.get("type", "").startswith("chat.response"):
                debug(
                    f"received {data.get('type')} request_id={payload.get('request_id')} "
                    f"expected={effective_request_id}"
                )
            if payload.get("request_id") != effective_request_id:
                continue
            delta = str(payload.get("delta") or "")
            if not delta:
                continue
            debug(f"received matching delta: {delta[:40]!r}")
            delta_count += 1
            response_text += delta
            first_token_ns = now_ns()
            return result(
                "backend_ui_sse",
                started_ns,
                first_token_ns,
                acknowledgement_ms=acknowledgement_ms,
                delta_count=delta_count,
                response_preview=response_text[:120],
                request_id=effective_request_id,
                thread_id=thread_id,
                endpoint=response_url,
                stream_endpoint=stream_url,
            )


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", choices=("direct", "runtime", "backend"))
    parser.add_argument(
        "--runtime-url", default=os.getenv("PROGENITOR_RUNTIME_URL", DEFAULT_RUNTIME_URL)
    )
    parser.add_argument(
        "--backend-url", default=os.getenv("MEASURE_BACKEND_URL", DEFAULT_BACKEND_URL)
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=float(os.getenv("MEASURE_TIMEOUT_SECONDS", "90")),
        help="Maximum wall time before returning a timeout result.",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.ERROR)
    try:
        if args.path == "direct":
            measured = await asyncio.wait_for(measure_direct(), args.timeout_seconds)
        elif args.path == "runtime":
            measured = await asyncio.wait_for(
                measure_runtime(args.runtime_url), args.timeout_seconds
            )
        else:
            measured = await asyncio.wait_for(
                measure_backend(args.backend_url), args.timeout_seconds
            )
    except TimeoutError:
        print(
            json.dumps(
                {
                    "path": args.path,
                    "completed": False,
                    "error": "measurement_timeout",
                    "timeout_seconds": args.timeout_seconds,
                },
                sort_keys=True,
            )
        )
        raise SystemExit(2)
    print(json.dumps(measured, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
