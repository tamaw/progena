"""Local Agent Protocol stub for the hello-world async subagent."""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from dataclasses import dataclass
from typing import Any

from aiohttp import web

from ..utils import utcnow_isoformat
from .tasks import HELLO_WORLD_ASYNC_ASSISTANT_ID, HELLO_WORLD_ASYNC_RESULT

_DEFAULT_DELAY_SECONDS = 2.0


@dataclass
class _ThreadRecord:
    thread_id: str
    values: dict[str, Any]
    created_at: str
    updated_at: str


@dataclass
class _RunRecord:
    run_id: str
    thread_id: str
    assistant_id: str
    status: str
    created_at: str
    updated_at: str
    error: str | None = None
    task: asyncio.Task[None] | None = None


class HelloWorldAsyncAgentProtocol:
    """Minimal Agent Protocol surface used by DeepAgents AsyncSubAgent."""

    def __init__(self, *, delay_seconds: float = _DEFAULT_DELAY_SECONDS) -> None:
        self._delay_seconds = delay_seconds
        self._threads: dict[str, _ThreadRecord] = {}
        self._runs: dict[tuple[str, str], _RunRecord] = {}
        self._lock = asyncio.Lock()

    def routes(self) -> list[web.RouteDef]:
        return [
            web.post("/agent-protocol/threads", self.handle_create_thread),
            web.get("/agent-protocol/threads/{thread_id}", self.handle_get_thread),
            web.post("/agent-protocol/threads/{thread_id}/runs", self.handle_create_run),
            web.get(
                "/agent-protocol/threads/{thread_id}/runs/{run_id}",
                self.handle_get_run,
            ),
            web.post(
                "/agent-protocol/threads/{thread_id}/runs/{run_id}/cancel",
                self.handle_cancel_run,
            ),
        ]

    async def stop(self) -> None:
        """Cancel any in-flight background completions."""
        async with self._lock:
            tasks = [record.task for record in self._runs.values() if record.task is not None]
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def handle_create_thread(self, request: web.Request) -> web.Response:
        payload = await self._safe_json(request)
        thread_id = str(payload.get("thread_id") or f"thread_{uuid.uuid4().hex[:12]}")
        now = utcnow_isoformat()
        async with self._lock:
            existing = self._threads.get(thread_id)
            if existing is None:
                self._threads[thread_id] = _ThreadRecord(
                    thread_id=thread_id,
                    values={"messages": []},
                    created_at=now,
                    updated_at=now,
                )
            else:
                existing.updated_at = now
        return web.json_response({"thread_id": thread_id, "created_at": now, "updated_at": now})

    async def handle_get_thread(self, request: web.Request) -> web.Response:
        thread_id = request.match_info["thread_id"]
        async with self._lock:
            thread = self._threads.get(thread_id)
            if thread is None:
                return web.json_response({"detail": "thread not found"}, status=404)
            payload = {
                "thread_id": thread.thread_id,
                "created_at": thread.created_at,
                "updated_at": thread.updated_at,
                "status": "idle",
                "values": thread.values,
            }
        return web.json_response(payload)

    async def handle_create_run(self, request: web.Request) -> web.Response:
        thread_id = request.match_info["thread_id"]
        payload = await self._safe_json(request)
        assistant_id = str(payload.get("assistant_id") or "").strip()
        if assistant_id != HELLO_WORLD_ASYNC_ASSISTANT_ID:
            return web.json_response({"detail": "unknown assistant"}, status=404)

        input_payload = payload.get("input") if isinstance(payload.get("input"), dict) else {}
        new_messages = input_payload.get("messages") if isinstance(input_payload, dict) else []
        multitask_strategy = str(payload.get("multitask_strategy") or "").strip().lower()
        run_id = f"run_{uuid.uuid4().hex[:12]}"
        now = utcnow_isoformat()

        async with self._lock:
            thread = self._threads.get(thread_id)
            if thread is None:
                return web.json_response({"detail": "thread not found"}, status=404)

            if multitask_strategy == "interrupt":
                for existing in list(self._runs.values()):
                    if existing.thread_id != thread_id or existing.status != "running":
                        continue
                    existing.status = "cancelled"
                    existing.updated_at = now
                    if existing.task is not None:
                        existing.task.cancel()

            if isinstance(new_messages, list):
                for message in new_messages:
                    if isinstance(message, dict):
                        thread.values.setdefault("messages", []).append(message)
            thread.updated_at = now

            record = _RunRecord(
                run_id=run_id,
                thread_id=thread_id,
                assistant_id=assistant_id,
                status="running",
                created_at=now,
                updated_at=now,
            )
            record.task = asyncio.create_task(self._complete_run(thread_id, run_id))
            self._runs[(thread_id, run_id)] = record

        return web.json_response(
            {
                "run_id": run_id,
                "thread_id": thread_id,
                "assistant_id": assistant_id,
                "status": "running",
                "created_at": now,
                "updated_at": now,
            }
        )

    async def handle_get_run(self, request: web.Request) -> web.Response:
        thread_id = request.match_info["thread_id"]
        run_id = request.match_info["run_id"]
        async with self._lock:
            record = self._runs.get((thread_id, run_id))
            if record is None:
                return web.json_response({"detail": "run not found"}, status=404)
            payload = {
                "run_id": record.run_id,
                "thread_id": record.thread_id,
                "assistant_id": record.assistant_id,
                "status": record.status,
                "created_at": record.created_at,
                "updated_at": record.updated_at,
            }
            if record.error:
                payload["error"] = record.error
        return web.json_response(payload)

    async def handle_cancel_run(self, request: web.Request) -> web.Response:
        thread_id = request.match_info["thread_id"]
        run_id = request.match_info["run_id"]
        async with self._lock:
            record = self._runs.get((thread_id, run_id))
            if record is None:
                return web.json_response({"detail": "run not found"}, status=404)
            record.status = "cancelled"
            record.updated_at = utcnow_isoformat()
            if record.task is not None:
                record.task.cancel()
        return web.json_response({"ok": True})

    async def _complete_run(self, thread_id: str, run_id: str) -> None:
        try:
            await asyncio.sleep(self._delay_seconds)
            async with self._lock:
                record = self._runs.get((thread_id, run_id))
                thread = self._threads.get(thread_id)
                if record is None or thread is None or record.status != "running":
                    return
                thread.values.setdefault("messages", []).append(
                    {"role": "assistant", "content": HELLO_WORLD_ASYNC_RESULT}
                )
                now = utcnow_isoformat()
                thread.updated_at = now
                record.status = "success"
                record.updated_at = now
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            async with self._lock:
                record = self._runs.get((thread_id, run_id))
                if record is None:
                    return
                record.status = "error"
                record.error = str(exc)
                record.updated_at = utcnow_isoformat()

    async def _safe_json(self, request: web.Request) -> dict[str, Any]:
        try:
            payload = await request.json()
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}
