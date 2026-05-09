"""Tests for Progenitor's persistent runtime HTTP server."""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

from app.progenitor.runtime.http import ProgenitorHTTPServer
from app.progenitor.state.models import A2ATaskRecord


class _StateManager:
    def __init__(self) -> None:
        self.tasks: dict[str, A2ATaskRecord] = {}

    async def get_current_state(self) -> dict:
        return {"thread_id": "thread-test", "cycle_count": 3}

    async def get_a2a_task(self, task_id: str) -> A2ATaskRecord | None:
        return self.tasks.get(task_id)

    async def upsert_a2a_task(self, record: A2ATaskRecord) -> None:
        self.tasks[record.task_id] = record


@pytest.mark.asyncio
async def test_http_server_serves_health_and_agent_card() -> None:
    state_manager = _StateManager()
    server = ProgenitorHTTPServer(state_manager=state_manager, port=0)
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
            assert body["name"] == "progenitor"
            assert body["endpoints"]["a2a_task_callback"].endswith("/a2a/tasks/callback")
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_http_server_records_a2a_task_callback() -> None:
    state_manager = _StateManager()
    loop_runner = MagicMock()
    loop_runner.queues.enqueue_a2a_completions.return_value = 1
    server = ProgenitorHTTPServer(
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
                f"{server.base_url}/a2a/tasks/callback?agent=observe_agent",
                json=payload,
            )

        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["task_id"] == "task-1"
        assert body["status"] == "completed"
        saved = state_manager.tasks["task-1"]
        assert saved.agent_name == "observe_agent"
        assert saved.summary == "Finished callback task."
        loop_runner.queues.enqueue_a2a_completions.assert_called_once_with([saved])
    finally:
        await server.stop()
