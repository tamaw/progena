"""Tests for tracked A2A task lifecycle helpers."""

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.progenitor.a2a.tasks import (
    dispatch_tracked_a2a_task,
    enqueue_completion_followup,
    refresh_tracked_a2a_task,
)
from app.progenitor.state.manager import StateManager
from app.progenitor.state.models import A2ATaskRecord
from app.progenitor.tools.get_a2a_task import GetA2ATaskTool
from app.progenitor.tools.protocol import ToolContext


@pytest.fixture
async def state_manager():
    with tempfile.TemporaryDirectory() as state_dir, tempfile.TemporaryDirectory() as logs_dir:
        manager = StateManager(Path(state_dir), Path(logs_dir))
        await manager.initialize()
        yield manager


async def test_dispatch_tracked_a2a_task_persists_submitted_task(
    monkeypatch, state_manager
) -> None:
    fake_client = MagicMock()
    fake_client.call_agent = AsyncMock(
        return_value={
            "success": True,
            "result": {
                "kind": "task",
                "id": "builder_task_1",
                "contextId": "ctx-1",
                "status": {
                    "state": "submitted",
                    "message": {"parts": [{"kind": "text", "text": "Task accepted."}]},
                },
            },
        }
    )
    monkeypatch.setattr("app.progenitor.a2a.tasks.A2AClient", lambda: fake_client)

    response, tracked_task = await dispatch_tracked_a2a_task(
        state_manager=state_manager,
        agent_name="builder_agent",
        agent_config={"url": "http://localhost:8107", "transport": "http"},
        request_text="Build a demo",
        request_metadata={"caller": "progenitor", "dispatch_mode": "async"},
        source="call_agent",
        timeout=5.0,
        blocking=False,
    )

    assert response["success"] is True
    assert tracked_task is not None
    assert tracked_task.task_id == "builder_task_1"
    assert tracked_task.status == "submitted"
    assert tracked_task.summary == "Task accepted."

    saved = await state_manager.get_a2a_task("builder_task_1")
    assert saved is not None
    assert saved.agent_name == "builder_agent"
    assert saved.request_text == "Build a demo"
    fake_client.call_agent.assert_awaited_once()
    assert fake_client.call_agent.await_args.kwargs["callback_url"] is None
    assert saved.request_metadata["a2a_task_id"] == "builder_task_1"
    assert saved.request_metadata["a2a_context_id"] == "ctx-1"

    kwargs = fake_client.call_agent.await_args.kwargs
    assert kwargs["context"]["dispatch_mode"] == "async"
    assert "a2a_task_id" not in kwargs["context"]
    assert "a2a_context_id" not in kwargs["context"]
    assert "task_id" not in kwargs
    assert "context_id" not in kwargs


async def test_dispatch_tracked_a2a_task_uses_a2a_push_callback_url(
    monkeypatch, state_manager
) -> None:
    fake_client = MagicMock()
    fake_client.call_agent = AsyncMock(
        return_value={
            "success": True,
            "result": {
                "kind": "task",
                "id": "builder_task_2",
                "contextId": "ctx-2",
                "status": {"state": "submitted"},
            },
        }
    )
    monkeypatch.setattr("app.progenitor.a2a.tasks.A2AClient", lambda: fake_client)

    await dispatch_tracked_a2a_task(
        state_manager=state_manager,
        agent_name="builder_agent",
        agent_config={
            "url": "http://localhost:8107",
            "transport": "http",
            "callback_url": "http://127.0.0.1:9501/a2a/tasks/callback",
        },
        request_text="Build a demo",
        request_metadata={"caller": "progenitor", "dispatch_mode": "async"},
        source="call_agent",
        timeout=5.0,
        blocking=False,
    )

    fake_client.call_agent.assert_awaited_once()
    assert (
        fake_client.call_agent.await_args.kwargs["callback_url"]
        == "http://127.0.0.1:9501/a2a/tasks/callback"
    )


@pytest.mark.asyncio
async def test_dispatch_tracked_a2a_task_ignores_client_supplied_ids(
    monkeypatch, state_manager
) -> None:
    fake_client = MagicMock()
    fake_client.call_agent = AsyncMock(
        return_value={
            "success": True,
            "result": {
                "kind": "task",
                "id": "observe_task_2",
                "contextId": "ctx-2",
                "status": {
                    "state": "submitted",
                    "message": {"parts": [{"kind": "text", "text": "Task accepted."}]},
                },
            },
        }
    )
    monkeypatch.setattr("app.progenitor.a2a.tasks.A2AClient", lambda: fake_client)

    response, tracked_task = await dispatch_tracked_a2a_task(
        state_manager=state_manager,
        agent_name="observe_agent",
        agent_config={"url": "http://localhost:8108", "transport": "http"},
        request_text="Inspect state",
        request_metadata={
            "caller": "progenitor",
            "a2a_task_id": "client-task",
            "a2a_context_id": "client-ctx",
        },
        source="call_agent",
        timeout=5.0,
        blocking=False,
    )

    assert response["success"] is True
    assert tracked_task is not None
    assert tracked_task.task_id == "observe_task_2"
    assert tracked_task.request_metadata["a2a_task_id"] == "observe_task_2"
    assert tracked_task.request_metadata["a2a_context_id"] == "ctx-2"

    kwargs = fake_client.call_agent.await_args.kwargs
    assert kwargs["context"] == {"caller": "progenitor"}


@pytest.mark.asyncio
async def test_refresh_tracked_a2a_task_updates_terminal_status(monkeypatch, state_manager) -> None:
    await state_manager.upsert_a2a_task(
        A2ATaskRecord(
            task_id="observe-1",
            agent_name="observe_agent",
            agent_url="http://localhost:8108",
            context_id="ctx-1",
            source="call_agent",
            request_text="Inspect state",
        )
    )

    fake_client = MagicMock()
    fake_client.get_task = AsyncMock(
        return_value={
            "success": True,
            "result": {
                "kind": "task",
                "id": "observe-1",
                "contextId": "ctx-1",
                "status": {
                    "state": "completed",
                    "message": {"parts": [{"kind": "text", "text": "Observe task completed."}]},
                },
            },
        }
    )
    monkeypatch.setattr("app.progenitor.a2a.tasks.A2AClient", lambda: fake_client)

    refreshed = await refresh_tracked_a2a_task(state_manager, "observe-1")

    assert refreshed.status == "completed"
    assert refreshed.summary == "Observe task completed."
    assert refreshed.completed_at is not None


@pytest.mark.asyncio
async def test_get_a2a_task_tool_surfaces_refresh_error(monkeypatch, state_manager) -> None:
    await state_manager.upsert_a2a_task(
        A2ATaskRecord(
            task_id="observe-2",
            agent_name="observe_agent",
            agent_url="http://localhost:8108",
            context_id="ctx-2",
            source="call_agent",
            request_text="Inspect logs",
            latest_task={
                "kind": "task",
                "id": "observe-2",
                "contextId": "ctx-2",
                "status": {
                    "state": "working",
                    "message": {"parts": [{"kind": "text", "text": "Still running."}]},
                },
            },
            summary="Still running.",
            latest_message="Still running.",
            status="working",
        )
    )
    refresh_mock = AsyncMock(side_effect=RuntimeError("agent unavailable"))
    monkeypatch.setattr("app.progenitor.tools.get_a2a_task.refresh_tracked_a2a_task", refresh_mock)

    tool = GetA2ATaskTool()
    result = await tool.execute(
        ToolContext(
            tool_registry=MagicMock(),
            llm_client=MagicMock(),
            state_manager=state_manager,
            tool_args={"task_id": "observe-2"},
        )
    )

    assert result.success is True
    assert result.data is not None
    assert result.data["task_id"] == "observe-2"
    assert result.data["refresh_error"] == "agent unavailable"


@pytest.mark.asyncio
async def test_enqueue_completion_followup_queues_same_thread_turn(state_manager) -> None:
    record = A2ATaskRecord(
        task_id="observe-3",
        agent_name="observe_agent",
        agent_url="http://localhost:8108",
        context_id="ctx-3",
        source="call_agent",
        request_text="Inspect pipeline",
        request_metadata={"thread_id": "thread-123"},
        status="completed",
        summary="Observe task completed.",
        latest_message="Observe task completed.",
    )
    await state_manager.upsert_a2a_task(record)

    queues = MagicMock()
    queues.enqueue_input = AsyncMock(return_value=True)

    sent = await enqueue_completion_followup(state_manager, queues, record)

    assert sent is True
    queues.enqueue_input.assert_awaited_once()
    args = queues.enqueue_input.await_args.args
    kwargs = queues.enqueue_input.await_args.kwargs
    assert "async external agent task" in args[0]
    assert "Observe task completed." in args[0]
    assert kwargs["metadata"]["thread_id"] == "thread-123"
    assert kwargs["metadata"]["notify_on_completion"] is True
    saved = await state_manager.get_a2a_task("observe-3")
    assert saved is not None
    assert saved.notification_sent_at is not None


@pytest.mark.asyncio
async def test_get_active_a2a_tasks_skips_notified_terminal_tasks(state_manager) -> None:
    from app.progenitor.utils import utcnow

    def record(task_id: str, status: str, **overrides) -> A2ATaskRecord:
        return A2ATaskRecord(
            task_id=task_id,
            agent_name="observe_agent",
            agent_url="http://localhost:8108",
            status=status,
            **overrides,
        )

    await state_manager.upsert_a2a_task(record("active-working", "working"))
    await state_manager.upsert_a2a_task(record("done-needs-notify", "completed"))
    await state_manager.upsert_a2a_task(
        record("done-notified", "completed", notification_sent_at=utcnow())
    )
    await state_manager.upsert_a2a_task(
        record("done-no-notify", "completed", notify_on_completion=False)
    )

    active = await state_manager.get_active_a2a_tasks()

    assert {task.task_id for task in active} == {"active-working", "done-needs-notify"}
