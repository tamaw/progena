"""Tests for tracked A2A task lifecycle helpers."""

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.progenitor.runtime.a2a_tasks import (
    A2ATaskTracker,
    dispatch_tracked_a2a_task,
    refresh_tracked_a2a_task,
    send_completion_notification,
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


@pytest.mark.asyncio
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
    monkeypatch.setattr("app.progenitor.runtime.a2a_tasks.A2AClient", lambda: fake_client)

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
    assert saved.request_metadata["a2a_task_id"] == "builder_task_1"
    assert saved.request_metadata["a2a_context_id"] == "ctx-1"

    kwargs = fake_client.call_agent.await_args.kwargs
    assert kwargs["context"]["dispatch_mode"] == "async"
    assert "a2a_task_id" not in kwargs["context"]
    assert "a2a_context_id" not in kwargs["context"]
    assert "task_id" not in kwargs
    assert "context_id" not in kwargs


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
    monkeypatch.setattr("app.progenitor.runtime.a2a_tasks.A2AClient", lambda: fake_client)

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
    monkeypatch.setattr("app.progenitor.runtime.a2a_tasks.A2AClient", lambda: fake_client)

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
async def test_send_completion_notification_pushes_agent_message(state_manager) -> None:
    record = A2ATaskRecord(
        task_id="observe-3",
        agent_name="observe_agent",
        agent_url="http://localhost:8108",
        context_id="ctx-3",
        source="call_agent",
        request_text="Inspect pipeline",
        status="completed",
        summary="Observe task completed.",
        latest_message="Observe task completed.",
    )
    await state_manager.upsert_a2a_task(record)

    llm_client = MagicMock()
    llm_client._push_event = AsyncMock()
    state_manager.get_current_state = AsyncMock(return_value={"run_id": "run-123"})

    sent = await send_completion_notification(state_manager, llm_client, record)

    assert sent is True
    llm_client._push_event.assert_awaited_once()
    args = llm_client._push_event.await_args.args
    assert args[0] == "agent_message"
    assert "observe agent task completed" in args[1]
    saved = await state_manager.get_a2a_task("observe-3")
    assert saved is not None
    assert saved.notification_sent_at is not None


@pytest.mark.asyncio
async def test_task_tracker_records_completion_callback_before_notifying(
    monkeypatch, state_manager
) -> None:
    await state_manager.upsert_a2a_task(
        A2ATaskRecord(
            task_id="observe-4",
            agent_name="observe_agent",
            agent_url="http://localhost:8108",
            context_id="ctx-4",
            source="call_agent",
            request_text="Inspect queue",
            status="working",
        )
    )
    refreshed = A2ATaskRecord(
        task_id="observe-4",
        agent_name="observe_agent",
        agent_url="http://localhost:8108",
        context_id="ctx-4",
        source="call_agent",
        request_text="Inspect queue",
        status="completed",
        summary="Observe task completed.",
        latest_message="Observe task completed.",
    )
    refresh_mock = AsyncMock(return_value=refreshed)
    notify_mock = AsyncMock(return_value=True)
    completion_callback = AsyncMock()
    llm_client = MagicMock()

    monkeypatch.setattr("app.progenitor.runtime.a2a_tasks.refresh_tracked_a2a_task", refresh_mock)
    monkeypatch.setattr(
        "app.progenitor.runtime.a2a_tasks.send_completion_notification", notify_mock
    )

    tracker = A2ATaskTracker(
        state_manager,
        llm_client=llm_client,
        completion_callback=completion_callback,
    )
    await tracker.poll_once()

    completion_callback.assert_awaited_once_with(refreshed)
    notify_mock.assert_awaited_once_with(state_manager, llm_client, refreshed)
