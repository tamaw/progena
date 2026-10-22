import asyncio

import pytest

from src.backend.events.documents import seed_document_types
from src.backend.storage.documents import InMemoryDocumentStore
from src.backend.workflows.service import (
    PromptExecutionResult,
    WorkflowExecutor,
    build_manual_trigger_event,
    get_workflow_run,
    save_workflow_definition,
)
from src.progenitor.workflows.documents import default_workflow_definition


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
        response = next(self.responses)
        if isinstance(response, PromptExecutionResult):
            return response
        return PromptExecutionResult(response=response, thread_id=kwargs["thread_id"])


def _executor(store, runner):
    return WorkflowExecutor(
        store_getter=lambda: store,
        prompt_runner=runner,
        emit_event=lambda *_args: None,
    )


@pytest.mark.asyncio
async def test_executor_runs_default_agent_from_event_data_without_thread_id():
    store = InMemoryDocumentStore()
    seed_document_types(store)
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

    assert runner.calls[0]["thread_id"] is not None
    assert "thread_id" not in completed.node_outputs[definition.graph.edges[0].from_.node]


@pytest.mark.asyncio
async def test_connected_agent_output_schedules_its_consumer_without_control_edge():
    store = InMemoryDocumentStore()
    seed_document_types(store)
    workflow = default_workflow_definition()
    event_id = _node_id(workflow, "event")
    first_id = _node_id(workflow, "agent")
    second_id = "agent_consumer"
    workflow["graph"]["nodes"][second_id] = {
        "kind": "agent",
        "title": "Consumer",
        "template": "Consume: {summary}",
        "input_schema": {
            "type": "object",
            "required": ["summary"],
            "properties": {"summary": {"type": "string"}},
        },
        "output_schema": {"type": "object", "properties": {}},
    }
    workflow["graph"]["edges"] = [
        {
            "from": {"node": event_id, "port": "key"},
            "to": {"node": first_id, "port": "key"},
        },
        {
            "from": {"node": first_id, "port": "message"},
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
async def test_pause_requested_while_an_agent_runs_stops_before_the_next_node():
    store = InMemoryDocumentStore()
    seed_document_types(store)
    workflow = default_workflow_definition()
    event_id = _node_id(workflow, "event")
    first_id = _node_id(workflow, "agent")
    second_id = "agent_consumer"
    workflow["graph"]["nodes"][second_id] = {
        "kind": "agent",
        "title": "Consumer",
        "template": "Consume: {summary}",
        "input_schema": {
            "type": "object",
            "required": ["summary"],
            "properties": {"summary": {"type": "string"}},
        },
        "output_schema": {"type": "object", "properties": {}},
    }
    workflow["graph"]["edges"] = [
        {"from": {"node": event_id, "port": "key"}, "to": {"node": first_id, "port": "key"}},
        {
            "from": {"node": first_id, "port": "message"},
            "to": {"node": second_id, "port": "summary"},
        },
    ]
    definition = save_workflow_definition(store, workflow)
    started = asyncio.Event()
    release = asyncio.Event()
    calls = []

    async def runner(**kwargs):
        calls.append(kwargs)
        started.set()
        await release.wait()
        return PromptExecutionResult(response='{"summary": "done"}', thread_id=kwargs["thread_id"])

    executor = _executor(store, runner)
    run = await executor.trigger_workflow(definition.id, build_manual_trigger_event(definition))
    await asyncio.wait_for(started.wait(), timeout=2)
    await asyncio.wait_for(executor.pause_workflow(definition.id), timeout=2)
    release.set()

    paused = await _wait_for(
        lambda: (
            get_workflow_run(store, run.run_id)
            if first_id in get_workflow_run(store, run.run_id).completed_node_ids
            else None
        )
    )
    await asyncio.sleep(0.05)

    paused = get_workflow_run(store, run.run_id)
    assert paused.status == "paused"
    assert paused.completed_node_ids == [first_id]
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_selected_condition_port_schedules_its_consumer_as_dataflow():
    store = InMemoryDocumentStore()
    seed_document_types(store)
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
    workflow["graph"]["nodes"][agent_id]["template"] = "Result: {result}"
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


@pytest.mark.asyncio
async def test_missing_placeholder_value_fails_step():
    store = InMemoryDocumentStore()
    seed_document_types(store)
    workflow = default_workflow_definition()
    agent_id = _node_id(workflow, "agent")
    workflow["graph"]["nodes"][agent_id]["template"] = "Hello {missing.x}"
    workflow["graph"]["nodes"]["data_missing"] = {
        "kind": "data",
        "title": "Missing",
        "value": {},
        "output_schema": {"type": "object"},
    }
    workflow["graph"]["edges"] = [
        {
            "from": {"node": "data_missing", "port": "value"},
            "to": {"node": agent_id, "port": "missing"},
        }
    ]
    definition = save_workflow_definition(store, workflow)
    runner = RecordingRunner(["ok"])
    executor = _executor(store, runner)

    run = await executor.trigger_workflow(definition.id, build_manual_trigger_event(definition))
    completed = await _wait_for(
        lambda: (
            get_workflow_run(store, run.run_id)
            if get_workflow_run(store, run.run_id).status in ("completed", "failed")
            else None
        )
    )

    assert completed.status == "failed"
    assert "missing value for {missing.x}" in completed.last_error


@pytest.mark.asyncio
async def test_per_run_thread_shared_by_nodes_and_overridden_by_wired_thread_id():
    store = InMemoryDocumentStore()
    seed_document_types(store)
    workflow = default_workflow_definition()
    event_id = _node_id(workflow, "event")
    first_id = _node_id(workflow, "agent")
    second_id = "agent_2"
    workflow["graph"]["nodes"][first_id]["template"] = "First: {key}"
    workflow["graph"]["nodes"][second_id] = {
        "kind": "agent",
        "title": "Second",
        "template": "Second: {key}",
        "input_schema": {"type": "object", "properties": {}},
        "output_schema": {"type": "object", "properties": {}},
    }
    workflow["graph"]["edges"] = [
        {"from": {"node": event_id, "port": "key"}, "to": {"node": first_id, "port": "key"}},
        {"from": {"node": event_id, "port": "key"}, "to": {"node": second_id, "port": "key"}},
    ]
    definition = save_workflow_definition(store, workflow)
    runner = RecordingRunner(["a", "b"])
    executor = _executor(store, runner)

    run = await executor.trigger_workflow(definition.id, build_manual_trigger_event(definition))
    completed = await _wait_for(
        lambda: (
            get_workflow_run(store, run.run_id)
            if get_workflow_run(store, run.run_id).status == "completed"
            else None
        )
    )

    assert completed.status == "completed"
    thread_id = runner.calls[0]["thread_id"]
    assert thread_id is not None
    assert runner.calls[1]["thread_id"] == thread_id


@pytest.mark.asyncio
async def test_wired_thread_id_overrides_per_run_thread():
    store = InMemoryDocumentStore()
    seed_document_types(store)
    workflow = default_workflow_definition()
    event_id = _node_id(workflow, "event")
    first_id = _node_id(workflow, "agent")
    second_id = "agent_2"
    workflow["graph"]["nodes"][first_id]["template"] = "First: {key}"
    workflow["graph"]["nodes"][second_id] = {
        "kind": "agent",
        "title": "Second",
        "template": "Second: {key}",
        "input_schema": {"type": "object", "properties": {}},
        "output_schema": {"type": "object", "properties": {}},
    }
    workflow["graph"]["nodes"]["thread_override"] = {
        "kind": "data",
        "title": "Thread",
        "value": "custom_thread_123",
        "output_schema": {"type": "string"},
    }
    workflow["graph"]["edges"] = [
        {"from": {"node": event_id, "port": "key"}, "to": {"node": first_id, "port": "key"}},
        {"from": {"node": event_id, "port": "key"}, "to": {"node": second_id, "port": "key"}},
        {
            "from": {"node": "thread_override", "port": "value"},
            "to": {"node": first_id, "port": "thread_id"},
        },
        {
            "from": {"node": "thread_override", "port": "value"},
            "to": {"node": second_id, "port": "thread_id"},
        },
    ]
    definition = save_workflow_definition(store, workflow)
    runner = RecordingRunner(["c", "d"])
    executor = _executor(store, runner)

    run = await executor.trigger_workflow(definition.id, build_manual_trigger_event(definition))
    await _wait_for(
        lambda: (
            get_workflow_run(store, run.run_id)
            if get_workflow_run(store, run.run_id).status == "completed"
            else None
        )
    )
    assert runner.calls[0]["thread_id"] == "custom_thread_123"
    assert runner.calls[1]["thread_id"] == "custom_thread_123"


@pytest.mark.asyncio
async def test_extract_returns_the_structured_reply_as_result():
    store = InMemoryDocumentStore()
    seed_document_types(store)
    workflow = default_workflow_definition()
    event_id = _node_id(workflow, "event")
    extract_id = "extract_1"
    agent_id = _node_id(workflow, "agent")
    workflow["graph"]["nodes"][extract_id] = {
        "kind": "extract",
        "title": "Extract",
        "template": "Extract: {key}",
        "schema": {"type": "string"},
    }
    del workflow["graph"]["nodes"][agent_id]
    workflow["graph"]["edges"] = [
        {"from": {"node": event_id, "port": "key"}, "to": {"node": extract_id, "port": "key"}},
    ]
    definition = save_workflow_definition(store, workflow)
    runner = RecordingRunner(['```json\n{"value": "structured_result"}\n```'])
    executor = _executor(store, runner)

    run = await executor.trigger_workflow(definition.id, build_manual_trigger_event(definition))
    completed = await _wait_for(
        lambda: (
            get_workflow_run(store, run.run_id)
            if get_workflow_run(store, run.run_id).status == "completed"
            else None
        )
    )

    assert completed.status == "completed"
    assert completed.node_outputs[extract_id]["result"] == "structured_result"
    sent = runner.calls[0]["input_text"]
    assert "structured_output" in sent and '"value"' in sent


@pytest.mark.asyncio
async def test_extract_fails_when_reply_is_not_json():
    store = InMemoryDocumentStore()
    seed_document_types(store)
    workflow = default_workflow_definition()
    event_id = _node_id(workflow, "event")
    extract_id = "extract_1"
    agent_id = _node_id(workflow, "agent")
    workflow["graph"]["nodes"][extract_id] = {
        "kind": "extract",
        "title": "Extract",
        "template": "Extract: {key}",
        "schema": {"type": "string"},
    }
    del workflow["graph"]["nodes"][agent_id]
    workflow["graph"]["edges"] = [
        {"from": {"node": event_id, "port": "key"}, "to": {"node": extract_id, "port": "key"}},
    ]
    definition = save_workflow_definition(store, workflow)
    runner = RecordingRunner(["Done! I rated it high."])
    executor = _executor(store, runner)

    run = await executor.trigger_workflow(definition.id, build_manual_trigger_event(definition))
    completed = await _wait_for(
        lambda: (
            get_workflow_run(store, run.run_id)
            if get_workflow_run(store, run.run_id).status in ("completed", "failed")
            else None
        )
    )

    assert completed.status == "failed"
    assert "reply was not the JSON result" in completed.last_error


@pytest.mark.asyncio
async def test_extract_fails_when_structured_output_invalid():
    store = InMemoryDocumentStore()
    seed_document_types(store)
    workflow = default_workflow_definition()
    event_id = _node_id(workflow, "event")
    extract_id = "extract_1"
    agent_id = _node_id(workflow, "agent")
    workflow["graph"]["nodes"][extract_id] = {
        "kind": "extract",
        "title": "Extract",
        "template": "Extract: {key}",
        "schema": {"type": "number"},
    }
    del workflow["graph"]["nodes"][agent_id]
    workflow["graph"]["edges"] = [
        {"from": {"node": event_id, "port": "key"}, "to": {"node": extract_id, "port": "key"}},
    ]
    definition = save_workflow_definition(store, workflow)
    runner = RecordingRunner(['{"value": "not_a_number"}'])
    executor = _executor(store, runner)

    run = await executor.trigger_workflow(definition.id, build_manual_trigger_event(definition))
    completed = await _wait_for(
        lambda: (
            get_workflow_run(store, run.run_id)
            if get_workflow_run(store, run.run_id).status in ("completed", "failed")
            else None
        )
    )

    assert completed.status == "failed"
    assert "failed schema validation" in completed.last_error


async def _completed(store, run_id):
    return await _wait_for(
        lambda: (
            get_workflow_run(store, run_id)
            if get_workflow_run(store, run_id).status in {"completed", "failed"}
            else None
        )
    )


@pytest.mark.asyncio
async def test_unwired_thread_id_placeholder_renders_the_run_thread():
    store = InMemoryDocumentStore()
    seed_document_types(store)
    workflow = default_workflow_definition()
    agent_id = _node_id(workflow, "agent")
    workflow["graph"]["nodes"][agent_id]["template"] = "Process {key} in {thread_id}"
    definition = save_workflow_definition(store, workflow)
    runner = RecordingRunner(["done"])

    run = await _executor(store, runner).trigger_workflow(
        definition.id, build_manual_trigger_event(definition)
    )
    completed = await _completed(store, run.run_id)

    assert completed.status == "completed", completed.error
    assert runner.calls[0]["input_text"].endswith(f" in {completed.thread_id}")


@pytest.mark.asyncio
async def test_agent_output_carries_the_thread_the_runner_used():
    store = InMemoryDocumentStore()
    seed_document_types(store)
    workflow = default_workflow_definition()
    agent_id = _node_id(workflow, "agent")
    definition = save_workflow_definition(store, workflow)

    async def allocating_runner(**kwargs):
        return PromptExecutionResult(response="ok", thread_id="allocated_thread")

    run = await _executor(store, allocating_runner).trigger_workflow(
        definition.id, build_manual_trigger_event(definition)
    )
    completed = await _completed(store, run.run_id)

    assert completed.node_outputs[agent_id]["thread_id"] == "allocated_thread"


@pytest.mark.asyncio
async def test_data_value_edited_mid_run_reaches_later_nodes():
    store = InMemoryDocumentStore()
    seed_document_types(store)
    workflow = default_workflow_definition()
    event_id = _node_id(workflow, "event")
    first_id = _node_id(workflow, "agent")
    workflow["graph"]["nodes"]["note"] = {
        "kind": "data",
        "title": "Note",
        "value": "old",
        "output_schema": {"type": "string"},
    }
    workflow["graph"]["nodes"]["second"] = {
        "kind": "agent",
        "title": "Second",
        "template": "After {ready}: {note}",
        "input_schema": {"type": "object", "properties": {}},
        "output_schema": {"type": "object", "properties": {}},
    }
    workflow["graph"]["edges"] = [
        {"from": {"node": event_id, "port": "key"}, "to": {"node": first_id, "port": "key"}},
        {"from": {"node": first_id, "port": "message"}, "to": {"node": "second", "port": "ready"}},
        {"from": {"node": "note", "port": "value"}, "to": {"node": "second", "port": "note"}},
    ]
    definition = save_workflow_definition(store, workflow)
    release = asyncio.Event()
    calls = []

    async def gated_runner(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            await release.wait()
        return PromptExecutionResult(response="ok", thread_id=kwargs["thread_id"])

    run = await _executor(store, gated_runner).trigger_workflow(
        definition.id, build_manual_trigger_event(definition)
    )
    await _wait_for(lambda: calls)
    edited = definition.model_dump(mode="json", by_alias=True)
    edited["graph"]["nodes"]["note"]["value"] = "new"
    save_workflow_definition(store, edited)
    release.set()
    completed = await _completed(store, run.run_id)

    assert completed.status == "completed", completed.error
    assert calls[1]["input_text"] == "After ok: new"


@pytest.mark.asyncio
async def test_format_node_renders_nested_and_non_string_values_without_a_runner():
    store = InMemoryDocumentStore()
    seed_document_types(store)
    workflow = default_workflow_definition()
    del workflow["graph"]["nodes"][_node_id(workflow, "agent")]
    workflow["graph"]["nodes"]["data"] = {
        "kind": "data",
        "title": "Input",
        "value": {"name": "Ada", "metadata": {"priority": 2, "tags": ["new"]}},
        "output_schema": {"type": "object"},
    }
    workflow["graph"]["nodes"]["format"] = {
        "kind": "format",
        "title": "Format",
        "template": "Hello {value.name}: {value.metadata}",
    }
    workflow["graph"]["edges"] = [
        {
            "from": {"node": "data", "port": "value"},
            "to": {"node": "format", "port": "value"},
        }
    ]
    definition = save_workflow_definition(store, workflow)
    executor = _executor(store, RecordingRunner([]))

    run = await executor.trigger_workflow(definition.id, build_manual_trigger_event(definition))
    completed = await _wait_for(
        lambda: (
            get_workflow_run(store, run.run_id)
            if get_workflow_run(store, run.run_id).status == "completed"
            else None
        )
    )

    assert completed.node_outputs["format"] == {
        "text": 'Hello Ada: {\n  "priority": 2,\n  "tags": [\n    "new"\n  ]\n}',
    }


def test_query_filter_tells_json_true_from_1():
    from src.backend.workflows.service import _execute_document_node
    from src.progenitor.workflows.models import WorkflowNodeDocumentQuery

    store = InMemoryDocumentStore()
    seed_document_types(store)
    store.save_document("progenitor.state", "kanban_card:yes", {"metadata": {"ready": True}})
    store.save_document("progenitor.state", "kanban_card:one", {"metadata": {"ready": 1}})
    node = WorkflowNodeDocumentQuery.model_validate(
        {
            "kind": "document_query",
            "title": "Ready",
            "document_type": "kanban_card",
            "filters": [{"field": "metadata", "op": "eq"}],
        }
    )

    output = _execute_document_node(store, node, {"metadata": {"ready": True}})

    assert output["keys"] == ["kanban_card:yes"]


@pytest.mark.asyncio
async def test_executor_records_step_and_run_token_usage():
    store = InMemoryDocumentStore()
    seed_document_types(store)
    workflow = default_workflow_definition()
    event_id = _node_id(workflow, "event")
    extract_id = "extract_1"
    agent_id = _node_id(workflow, "agent")
    final_agent_id = "agent_without_usage"
    workflow["graph"]["nodes"][extract_id] = {
        "kind": "extract",
        "title": "Extract",
        "template": "Extract: {key}",
        "schema": {"type": "string"},
    }
    workflow["graph"]["nodes"][agent_id].update(
        {
            "template": "Use extracted value: {result}",
            "input_schema": {
                "type": "object",
                "required": ["result"],
                "properties": {"result": {"type": "string"}},
            },
        }
    )
    workflow["graph"]["nodes"][final_agent_id] = {
        "kind": "agent",
        "title": "Finish",
        "template": "Finish: {message}",
        "input_schema": {
            "type": "object",
            "required": ["message"],
            "properties": {"message": {"type": "string"}},
        },
        "output_schema": {"type": "object", "properties": {}},
    }
    workflow["graph"]["edges"] = [
        {"from": {"node": event_id, "port": "key"}, "to": {"node": extract_id, "port": "key"}},
        {
            "from": {"node": extract_id, "port": "result"},
            "to": {"node": agent_id, "port": "result"},
        },
        {
            "from": {"node": agent_id, "port": "message"},
            "to": {"node": final_agent_id, "port": "message"},
        },
    ]
    definition = save_workflow_definition(store, workflow)
    extract_usage = {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5}
    agent_usage = {"input_tokens": 7, "output_tokens": 11, "total_tokens": 18}
    runner = RecordingRunner(
        [
            PromptExecutionResult(
                response='{"value": "structured_result"}',
                thread_id=None,
                usage=extract_usage,
            ),
            PromptExecutionResult(response="handled", thread_id=None, usage=agent_usage),
            PromptExecutionResult(response="finished", thread_id=None, usage=None),
        ]
    )

    run = await _executor(store, runner).trigger_workflow(
        definition.id, build_manual_trigger_event(definition)
    )
    completed = await _wait_for(
        lambda: (
            get_workflow_run(store, run.run_id)
            if get_workflow_run(store, run.run_id).status == "completed"
            else None
        )
    )

    assert [entry.get("tokens") for entry in completed.step_audit] == [
        extract_usage,
        agent_usage,
        None,
    ]
    assert completed.tokens == {"input_tokens": 9, "output_tokens": 14, "total_tokens": 23}
