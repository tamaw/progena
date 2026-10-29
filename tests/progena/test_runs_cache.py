from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver

import src.progena.runs._render as render_module
from src.progena.agent.filesystem.runs import RunDirectoryBackend
from src.progena.sessions.history import Conversation
from src.progena.sessions.transcript import ThreadMessages


class _NativeCheckpoints(InMemorySaver):
    """InMemorySaver with controllable checkpoint IDs and hydrated routine state."""

    def __init__(self) -> None:
        super().__init__()
        self.run_meta: dict[tuple[str, str], dict[str, Any]] = {}
        self.aget_calls = 0
        self._advance_after_get: tuple[str, str, dict[str, Any]] | None = None

    def advance(self, thread_id: str, namespace: str, run_meta: dict[str, Any]) -> None:
        checkpoints = self.storage.setdefault(thread_id, {}).setdefault(namespace, {})
        checkpoint_id = f"{len(checkpoints) + 1:032d}"
        checkpoints[checkpoint_id] = object()
        self.run_meta[thread_id, namespace] = run_meta

    def advance_after_next_get(
        self, thread_id: str, namespace: str, run_meta: dict[str, Any]
    ) -> None:
        self._advance_after_get = (thread_id, namespace, run_meta)

    async def aget_tuple(self, config: dict[str, Any]) -> Any:
        self.aget_calls += 1
        configurable = config["configurable"]
        thread_id = configurable["thread_id"]
        namespace = configurable.get("checkpoint_ns", "")
        checkpoints = self.storage.get(thread_id, {}).get(namespace, {})
        if not checkpoints:
            return None
        checkpoint_id = max(checkpoints)
        run_meta = self.run_meta.get((thread_id, namespace), {})
        if self._advance_after_get is not None:
            update = self._advance_after_get
            self._advance_after_get = None
            self.advance(*update)
        return SimpleNamespace(
            config={
                "configurable": {
                    "thread_id": thread_id,
                    "checkpoint_ns": namespace,
                    "checkpoint_id": checkpoint_id,
                }
            },
            checkpoint={"channel_values": {"run_meta": run_meta}},
            metadata={},
        )


class _RoutineState:
    def __init__(self, thread_id: str) -> None:
        self.thread_id = thread_id
        self.events = [
            {
                "request_id": "terminal-run",
                "status": "started",
                "timestamp": "2026-10-30T10:00:00+00:00",
                "agent_id": "cache-routine",
                "thread_id": thread_id,
            },
            {
                "request_id": "terminal-run",
                "status": "completed",
                "timestamp": "2026-10-30T10:00:01+00:00",
                "agent_id": "cache-routine",
                "thread_id": thread_id,
                "output": {"message": "done"},
            },
        ]
        self.graph = {
            "name": "Cache routine",
            "nodes": [{"id": "reply", "kind": "prompt", "label": "Reply"}],
        }

    async def get_preferences_config(self) -> dict[str, Any]:
        return {"timezone": "UTC"}

    async def get_routine_graph(self, _routine_id: str) -> dict[str, Any]:
        return self.graph

    async def list_routine_run_events(
        self, request_id: str | None = None, *, thread_ids: list[str] | None = None
    ) -> list[dict[str, Any]]:
        if thread_ids is not None:
            return [event for event in self.events if event.get("thread_id") in thread_ids]
        if request_id is not None:
            return [event for event in self.events if event.get("request_id") == request_id]
        return list(self.events)


class _ScheduleState:
    async def get_preferences_config(self) -> dict[str, Any]:
        return {"timezone": "UTC"}


def _run_meta(text: str) -> dict[str, Any]:
    return {
        "reply": [
            {
                "step": 1,
                "started_at": "2026-10-30T10:00:00+00:00",
                "output": text,
            }
        ]
    }


async def _routine_text(backend: RunDirectoryBackend) -> str:
    result = await backend.aread(
        "/runs/routines/2026-10-30_cache-routine_terminal-run.md", 0, 10_000
    )
    assert result.error is None
    return result.file_data["content"]


async def test_terminal_routine_cache_uses_compact_native_checkpoint_versions() -> None:
    thread_id = "routine:cache:root"
    checkpoints = _NativeCheckpoints()
    checkpoints.advance(thread_id, "", _run_meta("early result"))
    backend = RunDirectoryBackend(_RoutineState(thread_id), checkpoints)

    first = await _routine_text(backend)
    assert "early result" in first
    assert checkpoints.aget_calls == 1

    cached = await _routine_text(backend)
    assert "early result" in cached
    # A valid cache hit reads `storage` checkpoint IDs, not a hydrated checkpoint.
    assert checkpoints.aget_calls == 1

    checkpoints.advance(thread_id, "", _run_meta("late result"))
    refreshed = await _routine_text(backend)
    assert "late result" in refreshed
    assert checkpoints.aget_calls == 2


async def test_render_racing_a_checkpoint_advance_is_not_admitted_to_cache() -> None:
    thread_id = "routine:cache:race"
    checkpoints = _NativeCheckpoints()
    checkpoints.advance(thread_id, "", _run_meta("before race"))
    checkpoints.advance_after_next_get(thread_id, "", _run_meta("after race"))
    backend = RunDirectoryBackend(_RoutineState(thread_id), checkpoints)

    first = await _routine_text(backend)
    assert "before race" in first
    assert checkpoints.aget_calls == 1

    refreshed = await _routine_text(backend)
    assert "after race" in refreshed
    assert checkpoints.aget_calls == 2


async def test_schedule_cache_detects_late_nested_namespace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    thread_id = "schedule:cache"
    checkpoints = _NativeCheckpoints()
    checkpoints.advance(thread_id, "", {})
    loads = 0

    async def load_conversation(_checkpointer: Any, requested_thread: str) -> Conversation:
        nonlocal loads
        assert requested_thread == thread_id
        loads += 1
        root = ThreadMessages(
            messages=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "task",
                            "args": {"description": "delegate"},
                            "id": "call-1",
                            "type": "tool_call",
                        }
                    ],
                )
            ],
            committed_at={},
        )
        subagents = {}
        if "tools:child" in checkpoints.storage[thread_id]:
            subagents["call-1"] = ThreadMessages(
                messages=[AIMessage(content="late nested namespace")],
                committed_at={},
                nick="worker",
            )
        return Conversation(root=root, subagents=subagents, checkpoint_id="root")

    monkeypatch.setattr(render_module, "load_conversation", load_conversation)
    backend = RunDirectoryBackend(_ScheduleState(), checkpoints)
    payload = {
        "run_id": "schedule-run",
        "thread_id": thread_id,
        "status": "completed",
        "created_at": "2026-10-30T10:00:00+00:00",
        "started_at": "2026-10-30T10:00:00+00:00",
        "completed_at": "2026-10-30T10:00:01+00:00",
        "message": "scheduled message",
        "_title": "Cache schedule",
    }

    first, _ = await backend._render(await backend._context(), "schedule", "schedule.md", payload)
    assert "late nested namespace" not in first
    assert loads == 1

    cached, _ = await backend._render(await backend._context(), "schedule", "schedule.md", payload)
    assert "late nested namespace" not in cached
    assert loads == 1

    # The root tip has not changed; only a previously absent subagent namespace appeared.
    checkpoints.advance(thread_id, "tools:child", {})
    refreshed, _ = await backend._render(
        await backend._context(), "schedule", "schedule.md", payload
    )
    assert "late nested namespace" in refreshed
    assert loads == 2


class _WorkflowState:
    def __init__(self, thread_id: str) -> None:
        self.events = [
            {
                "request_id": "child-run",
                "status": "started",
                "timestamp": "2026-10-30T10:00:00+00:00",
                "agent_id": "cache-routine",
                "thread_id": thread_id,
                "session_id": "workflow_run_cache",
            },
            {
                "request_id": "child-run",
                "status": "completed",
                "timestamp": "2026-10-30T10:00:01+00:00",
                "agent_id": "cache-routine",
                "thread_id": thread_id,
                "session_id": "workflow_run_cache",
                "output": {"message": "done"},
            },
        ]
        self.graph = {
            "name": "Cache routine",
            "nodes": [{"id": "reply", "kind": "prompt", "label": "Reply"}],
        }

    async def get_preferences_config(self) -> dict[str, Any]:
        return {"timezone": "UTC"}

    async def get_routine_graph(self, _routine_id: str) -> dict[str, Any]:
        return self.graph

    async def get_workflow_definition(self, _workflow_id: str) -> dict[str, Any]:
        return {
            "title": "Cache workflow",
            "graph": {"nodes": {"child": {"kind": "routine", "title": "Child"}}},
        }

    async def list_routine_run_events(
        self, request_id: str | None = None, *, thread_ids: list[str] | None = None
    ) -> list[dict[str, Any]]:
        if thread_ids is not None:
            return [event for event in self.events if event.get("thread_id") in thread_ids]
        if request_id is not None:
            return [event for event in self.events if event.get("request_id") == request_id]
        return list(self.events)


async def test_workflow_cache_tracks_its_routine_child_checkpoint() -> None:
    thread_id = "routine:cache:workflow-child"
    checkpoints = _NativeCheckpoints()
    checkpoints.advance(thread_id, "", _run_meta("early child result"))
    state = _WorkflowState(thread_id)
    state.events = [event for event in state.events if event["status"] == "started"]
    backend = RunDirectoryBackend(state, checkpoints)
    payload = {
        "run_id": "workflow-run",
        "workflow_id": "workflow",
        "status": "failed",
        "started_at": "2026-10-30T10:00:00+00:00",
        "completed_at": "2026-10-30T10:00:01+00:00",
        "step_audit": [
            {
                "node_id": "child",
                "kind": "routine",
                "thread_id": thread_id,
                "request_id": "child-run",
                "started_at": "2026-10-30T10:00:00+00:00",
                "completed_at": "2026-10-30T10:00:01+00:00",
            }
        ],
        "node_outputs": {},
        "_title": "Cache workflow",
    }

    first, _ = await backend._render(await backend._context(), "workflow", "workflow.md", payload)
    assert "early child result" in first
    assert checkpoints.aget_calls == 1

    cached, _ = await backend._render(await backend._context(), "workflow", "workflow.md", payload)
    assert "early child result" in cached
    assert checkpoints.aget_calls == 1

    state.events.append(
        {
            "request_id": "child-run",
            "status": "failed",
            "timestamp": "2026-10-30T10:00:01+00:00",
            "agent_id": "cache-routine",
            "thread_id": thread_id,
            "session_id": "workflow_run_cache",
            "error": "Child failure arrived after its parent settled",
        }
    )
    settled, _ = await backend._render(await backend._context(), "workflow", "workflow.md", payload)
    assert "Child failure arrived after its parent settled" in settled
    assert checkpoints.aget_calls == 2

    checkpoints.advance(thread_id, "", _run_meta("late child result"))
    refreshed, _ = await backend._render(
        await backend._context(), "workflow", "workflow.md", payload
    )
    assert "late child result" in refreshed
    assert checkpoints.aget_calls == 3


async def test_terminal_routine_cache_observes_its_first_checkpoint() -> None:
    thread_id = "routine:cache:initially-missing"
    checkpoints = _NativeCheckpoints()
    backend = RunDirectoryBackend(_RoutineState(thread_id), checkpoints)
    first = await _routine_text(backend)
    assert "First checkpoint arrived" not in first
    checkpoints.advance(thread_id, "", _run_meta("First checkpoint arrived"))
    refreshed = await _routine_text(backend)
    assert "First checkpoint arrived" in refreshed
