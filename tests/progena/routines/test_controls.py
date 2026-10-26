"""Deterministic native-task control regressions."""

from __future__ import annotations

import asyncio

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver

from src.progena.routines.controls import (
    RoutineRunCancelled,
    RoutineRunControlConflict,
    RoutineRunControlExit,
    RoutineRunController,
)
from src.progena.routines.revisions import NullRoutineRevisionReader
from src.progena.routines.subagent import create_routine_executor


class _StateManager:
    def __init__(self) -> None:
        self.controls: dict[tuple[str, str], dict] = {}
        self.events: list[dict] = []

    async def get_routine_run_control(self, agent_id: str, session_id: str):
        return self.controls.get((agent_id, session_id))

    async def save_routine_run_control(self, payload: dict) -> None:
        self.controls[(payload["agent_id"], payload["session_id"])] = dict(payload)

    async def save_routine_run_event(self, payload: dict) -> None:
        self.events.append(dict(payload))


class _NativeStateManager(_StateManager):
    def __init__(self, graph: dict) -> None:
        super().__init__()
        self.graph = graph

    async def get_routine_graph(self, routine_id: str) -> dict | None:
        return self.graph if routine_id == self.graph["id"] else None

    async def get_model_profiles_config(self) -> dict:
        return {}

    async def get_execution_policy(self, policy_id: str):
        from src.progena.settings.policies import ExecutionPolicy

        return ExecutionPolicy(id=policy_id, name="Default").model_dump(mode="json")


class _PinnedReader(NullRoutineRevisionReader):
    def __init__(self, graph: dict) -> None:
        self.graph = graph

    async def fetch(self, routine_id: str, revision_id: str) -> dict | None:
        return self.graph if (routine_id, revision_id) == (self.graph["id"], "revision-1") else None

    async def fetch_revision_id_for_content(self, routine_id: str, graph: dict) -> str | None:
        return "revision-1" if routine_id == self.graph["id"] and graph == self.graph else None


def _execution(request_id: str = "attempt-1") -> dict[str, str]:
    return {
        "agent_id": "demo",
        "thread_id": "routine:demo:revision-1:session-1",
        "session_id": "session-1",
        "request_id": request_id,
    }


@pytest.mark.asyncio
async def test_stop_is_pending_until_native_task_terminates_then_exact_thread_can_resume():
    manager = _StateManager()
    controls = RoutineRunController(manager)
    entered = asyncio.Event()

    async def block() -> dict:
        entered.set()
        await asyncio.Event().wait()
        return {"unexpected": True}

    first = asyncio.create_task(controls.run_graph_execution(_execution(), block()))
    await entered.wait()
    pending = await controls.control_routine_run(
        "demo",
        thread_id=_execution()["thread_id"],
        request_id="attempt-1",
        action="stop",
    )
    assert pending["status"] == "stopping"
    assert manager.controls[("demo", "session-1")]["status"] == "stopping"
    assert manager.events[-1]["status"] == "stopping"
    assert not any(event["status"] in {"stopped", "cancelled"} for event in manager.events)

    with pytest.raises(RoutineRunControlExit, match="stopped"):
        await first
    assert manager.events[-1]["status"] == "stopped"
    assert manager.controls[("demo", "session-1")]["status"] == "stopped"

    resumed_execution = _execution("attempt-2")

    async def continue_checkpoint() -> dict:
        return {"continued": True}

    resumed = await controls.run_graph_execution(
        resumed_execution,
        continue_checkpoint(),
        resume_thread_id=resumed_execution["thread_id"],
    )
    assert resumed == {"continued": True}
    await controls.complete_execution(resumed_execution, "completed")
    assert manager.controls[("demo", "session-1")]["status"] == "completed"


@pytest.mark.asyncio
async def test_nested_stop_escalates_to_root_and_settles_all_live_lifecycles() -> None:
    manager = _StateManager()
    controls = RoutineRunController(manager)
    child_execution = {
        "agent_id": "child",
        "thread_id": "routine:child:revision-1:child-session",
        "session_id": "child-session",
        "request_id": "child-attempt",
    }
    child_active = asyncio.Event()
    parent_settled = asyncio.Event()

    async def parent_graph() -> dict:
        await controls.register_nested_execution(child_execution)
        child_active.set()
        await asyncio.Event().wait()
        parent_settled.set()
        return {}

    root = asyncio.create_task(controls.run_graph_execution(_execution(), parent_graph()))
    await child_active.wait()
    pending = await controls.control_routine_run(
        "child",
        thread_id=child_execution["thread_id"],
        request_id=child_execution["request_id"],
        action="stop",
    )
    assert pending["status"] == "stopping"
    with pytest.raises(RoutineRunControlExit, match="stopped"):
        await root
    assert not parent_settled.is_set()
    assert manager.controls[("demo", "session-1")]["status"] == "stopped"
    assert manager.controls[("child", "child-session")]["status"] == "stopped"
    assert {event["agent_id"] for event in manager.events} == {"demo", "child"}


@pytest.mark.asyncio
async def test_suppressed_cancellation_stays_pending_and_records_its_actual_outcome():
    manager = _StateManager()
    controls = RoutineRunController(manager)
    entered = asyncio.Event()
    cancellation_delivered = asyncio.Event()
    release = asyncio.Event()
    execution = _execution()

    async def suppress_cancel() -> dict:
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancellation_delivered.set()
            await release.wait()
            return {"finished_after_cancel_request": True}

    task = asyncio.create_task(controls.run_graph_execution(execution, suppress_cancel()))
    await entered.wait()
    assert (
        await controls.control_routine_run(
            "demo",
            thread_id=execution["thread_id"],
            request_id=execution["request_id"],
            action="stop",
        )
    )["status"] == "stopping"
    await cancellation_delivered.wait()
    assert manager.controls[("demo", "session-1")]["status"] == "stopping"
    assert manager.events[-1]["status"] == "stopping"
    assert not any(event["status"] in {"stopped", "cancelled"} for event in manager.events)

    release.set()
    assert await task == {"finished_after_cancel_request": True}
    await controls.complete_execution(execution, "completed")
    assert manager.controls[("demo", "session-1")]["status"] == "completed"
    assert not any(event["status"] in {"stopped", "cancelled"} for event in manager.events)


@pytest.mark.asyncio
async def test_cancel_is_irreversible_and_unknown_or_wrong_attempt_is_conflict():
    manager = _StateManager()
    controls = RoutineRunController(manager)
    execution = _execution()
    entered = asyncio.Event()

    async def block() -> dict:
        entered.set()
        await asyncio.Event().wait()
        return {}

    with pytest.raises(RoutineRunControlConflict):
        await controls.control_routine_run(
            "demo", thread_id=execution["thread_id"], request_id="missing", action="stop"
        )

    task = asyncio.create_task(controls.run_graph_execution(execution, block()))
    await entered.wait()
    with pytest.raises(RoutineRunControlConflict):
        await controls.control_routine_run(
            "demo", thread_id=execution["thread_id"], request_id="wrong-attempt", action="stop"
        )
    assert (
        await controls.control_routine_run(
            "demo",
            thread_id=execution["thread_id"],
            request_id=execution["request_id"],
            action="cancel",
        )
    )["status"] == "cancelling"
    with pytest.raises(RoutineRunControlExit, match="cancelled"):
        await task
    assert manager.events[-1]["status"] == "cancelled"

    with pytest.raises(RoutineRunCancelled):
        await controls.assert_not_cancelled("demo", "session-1")
    with pytest.raises(RoutineRunCancelled):
        await controls.run_graph_execution(
            _execution("attempt-2"),
            asyncio.sleep(0, result={}),
            resume_thread_id=execution["thread_id"],
        )


@pytest.mark.asyncio
async def test_escalating_stop_to_cancel_does_not_interrupt_native_teardown():
    manager = _StateManager()
    controls = RoutineRunController(manager)
    execution = _execution()
    identity = {"thread_id": execution["thread_id"], "request_id": execution["request_id"]}
    entered = asyncio.Event()
    tearing_down = asyncio.Event()
    release = asyncio.Event()
    drained: list[bool] = []

    async def graph() -> dict:
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            tearing_down.set()
            await release.wait()
            drained.append(True)
            raise
        return {}

    task = asyncio.create_task(controls.run_graph_execution(execution, graph()))
    await entered.wait()
    await controls.control_routine_run("demo", **identity, action="stop")
    await tearing_down.wait()
    escalated = await controls.control_routine_run("demo", **identity, action="cancel")
    assert escalated["status"] == "cancelling"
    release.set()

    with pytest.raises(RoutineRunControlExit, match="cancelled"):
        await task
    assert drained == [True]
    assert manager.controls[("demo", "session-1")]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_claimed_resume_admits_only_its_request_and_failed_start_releases_it():
    manager = _StateManager()
    controls = RoutineRunController(manager)
    stopped = {**_execution(), "status": "stopped", "timestamp": "2026-10-26T00:00:00Z"}
    manager.controls[("demo", "session-1")] = {
        **stopped,
        "status": "resuming",
        "request_id": "attempt-2",
        "resumed_from": stopped,
    }
    thread_id = stopped["thread_id"]

    with pytest.raises(RoutineRunControlConflict, match="another resume"):
        await controls.assert_startable(_execution("attempt-3"), resume_thread_id=thread_id)
    await controls.assert_startable(_execution("attempt-2"), resume_thread_id=thread_id)

    await controls.release_resume_claim(_execution("attempt-3"))
    assert manager.controls[("demo", "session-1")]["status"] == "resuming"
    await controls.release_resume_claim(_execution("attempt-2"))
    assert manager.controls[("demo", "session-1")] == stopped


@pytest.mark.asyncio
async def test_native_stop_then_resume_reuses_checkpoint_without_repeating_completed_node():
    graph = {
        "id": "demo",
        "name": "Demo",
        "description": "control regression",
        "version": "2.0.0",
        "entry": "first",
        "nodes": [
            {
                "id": "first",
                "kind": "eval",
                "output_key": "first",
                "config": {"expression": "1"},
            },
            {
                "id": "block",
                "kind": "agent",
                "output_key": "block",
                "config": {"agent_name": "worker", "prompt_template": "{request}"},
            },
        ],
        "edges": [{"from": "first", "to": "block", "level": "normal"}],
    }

    class _BlockingAgent:
        def __init__(self) -> None:
            self.calls = 0
            self.entered = asyncio.Event()

        async def ainvoke(self, state, *, config):
            self.calls += 1
            if self.calls == 1:
                self.entered.set()
                await asyncio.Event().wait()
            return {"messages": [AIMessage(content="done")]}

    manager = _NativeStateManager(graph)
    controls = RoutineRunController(manager)
    saver = MemorySaver()
    worker = _BlockingAgent()
    executor = create_routine_executor(
        routine_id="demo",
        model=object(),
        state_manager=manager,
        revision_reader=_PinnedReader(graph),
        agent_registry={"worker": worker},
        routine_registry={},
        checkpointer=saver,
        routine_controls=controls,
    )

    first = asyncio.create_task(
        executor.ainvoke(
            {
                "_interactive_session_id": "session-1",
                "_routine_request_id": "attempt-1",
            }
        )
    )
    await worker.entered.wait()
    started = next(event for event in manager.events if event["status"] == "started")
    assert (
        await controls.control_routine_run(
            "demo",
            thread_id=started["thread_id"],
            request_id="attempt-1",
            action="stop",
        )
    )["status"] == "stopping"
    with pytest.raises(RoutineRunControlExit, match="stopped"):
        await first

    resumed = await executor.ainvoke(
        {
            "_interactive_session_id": "session-1",
            "_routine_resume_thread_id": started["thread_id"],
            "_routine_request_id": "attempt-2",
        }
    )
    assert resumed["_routine_thread_id"] == started["thread_id"]
    assert worker.calls == 2
    checkpoint = await saver.aget_tuple({"configurable": {"thread_id": started["thread_id"]}})
    assert len(checkpoint.checkpoint["channel_values"]["run_meta"]["first"]) == 1
