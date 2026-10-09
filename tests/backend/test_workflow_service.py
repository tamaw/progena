import asyncio

import pytest

from app.backend.document_store import InMemoryDocumentStore
from app.backend.workflow_service import (
    PromptExecutionResult,
    WorkflowExecutor,
    build_manual_trigger_event,
    get_workflow_run,
    save_workflow_definition,
)
from app.progenitor.workflows import default_workflow_definition


async def _wait_for(predicate, timeout=2.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if value := predicate():
            return value
        await asyncio.sleep(0.01)
    raise AssertionError("Timed out waiting for workflow execution")


def _node_id(workflow, kind):
    return next(
        node_id for node_id, node in workflow["graph"]["nodes"].items() if node["kind"] == kind
    )


class RecordingRunner:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return PromptExecutionResult(response=next(self.responses), thread_id=kwargs["thread_id"])


def _executor(store, runner):
    return WorkflowExecutor(
        store_getter=lambda: store,
        prompt_runner=runner,
        emit_event=lambda *_args: None,
    )


@pytest.mark.asyncio
async def test_executor_runs_default_agent_from_event_data_without_thread_id():
    store = InMemoryDocumentStore()
    workflow = default_workflow_definition()
    definition = save_workflow_definition(store, workflow)
    runner = RecordingRunner(["{}"])
    executor = _executor(store, runner)
    trigger = build_manual_trigger_event(definition)

    run = await executor.trigger_workflow(definition.id, trigger)
    completed = await _wait_for(
        lambda: (
            get_workflow_run(store, run.run_id)
            if get_workflow_run(store, run.run_id).status == "completed"
            else None
        )
    )

    assert runner.calls[0]["thread_id"] is None
    assert "thread_id" not in completed.node_outputs[definition.graph.edges[0].from_.node]


@pytest.mark.asyncio
async def test_document_changed_event_node_filters_and_receives_flat_event():
    store = InMemoryDocumentStore()
    workflow = default_workflow_definition(event_type="document.changed")
    event_id = _node_id(workflow, "event")
    workflow["graph"]["nodes"][event_id].update(key_prefix="routine:", action="upserted")
    definition = save_workflow_definition(store, workflow)
    runner = RecordingRunner(["{}"])
    executor = _executor(store, runner)
    event = {
        "event_type": "document.changed",
        "timestamp": "2026-10-10T00:00:00+00:00",
        "key_prefix": "routine:",
        "key": "routine:abc",
        "action": "upserted",
    }

    assert await executor.dispatch_event("document.changed", {**event, "action": "deleted"}) == []
    assert (
        await executor.dispatch_event("document.changed", {**event, "key_prefix": "policy:"}) == []
    )
    (run,) = await executor.dispatch_event("document.changed", event)

    completed = await _wait_for(
        lambda: (
            get_workflow_run(store, run.run_id)
            if get_workflow_run(store, run.run_id).status == "completed"
            else None
        )
    )
    assert completed.workflow_id == definition.id
    assert completed.source == "document"
    assert completed.trigger_node_id == event_id
    assert completed.node_outputs[event_id]["key"] == "routine:abc"


@pytest.mark.asyncio
async def test_connected_agent_output_schedules_its_consumer_without_control_edge():
    store = InMemoryDocumentStore()
    workflow = default_workflow_definition()
    event_id = _node_id(workflow, "event")
    first_id = _node_id(workflow, "agent")
    second_id = "agent_consumer"
    workflow["graph"]["nodes"][first_id]["input_schema"] = {
        "type": "object",
        "required": ["event_type"],
        "properties": {"event_type": {"type": "string"}},
    }
    workflow["graph"]["nodes"][first_id]["output_schema"] = {
        "type": "object",
        "required": ["summary"],
        "properties": {"summary": {"type": "string"}},
    }
    workflow["graph"]["nodes"][second_id] = {
        "kind": "agent",
        "title": "Consumer",
        "message": "Consume it.",
        "input_schema": {
            "type": "object",
            "required": ["summary"],
            "properties": {"summary": {"type": "string"}},
        },
        "output_schema": {"type": "object", "properties": {}},
    }
    workflow["graph"]["edges"] = [
        {
            "from": {"node": event_id, "port": "event_type"},
            "to": {"node": first_id, "port": "event_type"},
        },
        {
            "from": {"node": first_id, "port": "summary"},
            "to": {"node": second_id, "port": "summary"},
        },
    ]
    definition = save_workflow_definition(store, workflow)
    runner = RecordingRunner(['{"summary": "done"}', "{}"])
    executor = _executor(store, runner)

    run = await executor.trigger_workflow(definition.id, build_manual_trigger_event(definition))
    completed = await _wait_for(
        lambda: (
            get_workflow_run(store, run.run_id)
            if get_workflow_run(store, run.run_id).status == "completed"
            else None
        )
    )

    assert completed.completed_node_ids == [first_id, second_id]
    assert '"summary": "done"' in runner.calls[1]["input_text"]


@pytest.mark.asyncio
async def test_selected_condition_port_schedules_its_consumer_as_dataflow():
    store = InMemoryDocumentStore()
    workflow = default_workflow_definition()
    agent_id = _node_id(workflow, "agent")
    condition_id = "condition_gate"
    workflow["graph"]["nodes"][condition_id] = {
        "kind": "condition",
        "title": "Gate",
        "expression": "ready",
        "input_schema": {
            "type": "object",
            "required": ["ready"],
            "properties": {"ready": {"type": "boolean"}},
        },
    }
    workflow["graph"]["nodes"]["data_ready"] = {
        "kind": "data",
        "title": "Ready",
        "value": True,
        "output_schema": {"type": "boolean"},
    }
    workflow["graph"]["nodes"][agent_id]["input_schema"] = {
        "type": "object",
        "required": ["result"],
        "properties": {"result": {"type": "boolean"}},
    }
    workflow["graph"]["edges"] = [
        {
            "from": {"node": "data_ready", "port": "value"},
            "to": {"node": condition_id, "port": "ready"},
        },
        {
            "from": {"node": condition_id, "port": "true"},
            "to": {"node": agent_id, "port": "result"},
        },
    ]
    definition = save_workflow_definition(store, workflow)
    runner = RecordingRunner(["{}"])
    executor = _executor(store, runner)

    run = await executor.trigger_workflow(definition.id, build_manual_trigger_event(definition))
    completed = await _wait_for(
        lambda: (
            get_workflow_run(store, run.run_id)
            if get_workflow_run(store, run.run_id).status == "completed"
            else None
        )
    )

    assert completed.completed_node_ids == [condition_id, agent_id]
    assert len(runner.calls) == 1
