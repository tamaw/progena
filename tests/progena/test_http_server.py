"""Tests for Progena's persistent runtime HTTP server."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import httpx
import pytest

from src.progena.a2a.models import A2ATaskRecord
from src.progena.async_subagents.tasks import HELLO_WORLD_ASYNC_RESULT
from src.progena.runtime.http.server import ProgenaHTTPServer
from tests.progena.conftest import _FakeRuntimeStore


class _StateManager:
    def __init__(self) -> None:
        self.tasks: dict[str, A2ATaskRecord] = {}
        self._runtime_store = _FakeRuntimeStore()

    async def get_current_state(self) -> dict:
        return {"thread_id": "thread-test", "cycle_count": 3}

    async def get_a2a_task(self, task_id: str) -> A2ATaskRecord | None:
        return self.tasks.get(task_id)

    async def upsert_a2a_task(self, record: A2ATaskRecord) -> None:
        self.tasks[record.task_id] = record


@pytest.mark.asyncio
async def test_http_server_serves_health_and_agent_card() -> None:
    state_manager = _StateManager()
    server = ProgenaHTTPServer(state_manager=state_manager, port=0)
    await server.start()
    try:
        assert server.bound_port is not None
        async with httpx.AsyncClient() as client:
            health = await client.get(f"{server.base_url}/health")
            assert health.status_code == 200
            assert health.json()["thread_id"] == "thread-test"

            card = await client.get(f"{server.base_url}/.well-known/agent-card.json")
            assert card.status_code == 200
            body = card.json()
            assert body["name"] == "progena"
            assert body["endpoints"]["a2a_task_callback"].endswith("/a2a/tasks/callback")
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_http_server_records_a2a_task_callback() -> None:
    state_manager = _StateManager()
    loop_runner = MagicMock()
    loop_runner.queues.enqueue_a2a_completions.return_value = 1
    server = ProgenaHTTPServer(
        state_manager=state_manager,
        loop_runner=loop_runner,
        port=0,
    )
    await server.start()
    try:
        payload = {
            "kind": "task",
            "id": "task-1",
            "contextId": "ctx-1",
            "status": {
                "state": "completed",
                "message": {"parts": [{"text": "Finished callback task."}]},
            },
        }
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{server.base_url}/a2a/tasks/callback?agent=example_agent",
                json=payload,
            )

        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["task_id"] == "task-1"
        assert body["status"] == "completed"
        saved = state_manager.tasks["task-1"]
        assert saved.agent_name == "example_agent"
        assert saved.summary == "Finished callback task."
        loop_runner.queues.enqueue_a2a_completions.assert_called_once_with([saved])
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_http_server_serves_hello_world_async_agent_protocol() -> None:
    state_manager = _StateManager()
    server = ProgenaHTTPServer(state_manager=state_manager, port=0)
    await server.start()
    try:
        async with httpx.AsyncClient() as client:
            thread = await client.post(f"{server.base_url}/agent-protocol/threads", json={})
            assert thread.status_code == 200
            thread_id = thread.json()["thread_id"]

            run = await client.post(
                f"{server.base_url}/agent-protocol/threads/{thread_id}/runs",
                json={
                    "assistant_id": "hello-world-async",
                    "input": {"messages": [{"role": "user", "content": "hello"}]},
                },
            )
            assert run.status_code == 200
            run_id = run.json()["run_id"]

            status = ""
            for _ in range(20):
                response = await client.get(
                    f"{server.base_url}/agent-protocol/threads/{thread_id}/runs/{run_id}"
                )
                assert response.status_code == 200
                status = response.json()["status"]
                if status == "success":
                    break
                await asyncio.sleep(0.2)

            assert status == "success"
            thread_state = await client.get(f"{server.base_url}/agent-protocol/threads/{thread_id}")
            messages = thread_state.json()["values"]["messages"]
            assert messages[-1]["content"] == HELLO_WORLD_ASYNC_RESULT
    finally:
        await server.stop()
