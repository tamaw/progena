"""Execution-boundary regressions: run isolation, resume and structured delegation."""

import asyncio
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.errors import NodeTimeoutError

from src.progenitor.routines.revisions import NullRoutineRevisionReader
from src.progenitor.routines.subagent import (
    create_routine_executor,
    create_routine_subagent,
    resume_routine_thread,
)


class Documents:
    async def get_model_profiles_config(self):
        return {}

    async def get_execution_policy(self, policy_id):
        from src.progenitor.settings.policies import ExecutionPolicy

        return ExecutionPolicy(id=policy_id, name="Default").model_dump(mode="json")

    def __init__(self, *graphs):
        self.graphs = {graph["id"]: graph for graph in graphs}

    async def get_routine_graph(self, routine_id):
        return self.graphs.get(routine_id)


class EventDocuments(Documents):
    def __init__(self, *graphs):
        super().__init__(*graphs)
        self.events = []

    async def save_routine_run_event(self, payload):
        self.events.append(payload)


@pytest.mark.asyncio
async def test_lifecycle_pauses_and_resumes_same_thread_with_distinct_attempts():
    doc = graph(
        "events",
        [
            {"id": "one", "kind": "eval", "config": {"expression": "1"}},
            {"id": "two", "kind": "eval", "config": {"expression": "one + 1"}},
        ],
        [{"from": "one", "to": "two", "level": "normal"}],
    )
    manager, saver = EventDocuments(doc), MemorySaver()
    run = executor(doc, manager, checkpointer=saver)
    first = await run.ainvoke(
        {
            "_interactive_session_id": "shared",
            "_routine_request_id": "first",
            "_interactive_target_node": "one",
        }
    )
    assert [e["status"] for e in manager.events] == ["started", "paused"]
    checkpoint = await saver.aget_tuple(
        {"configurable": {"thread_id": first["_routine_thread_id"]}}
    )
    assert checkpoint.checkpoint["channel_values"]["data"]["one"] == 1
    second = await run.ainvoke(
        {"_interactive_session_id": "shared", "_routine_request_id": "second"}
    )
    assert [e["status"] for e in manager.events] == ["started", "paused", "started", "completed"]
    assert first["_routine_thread_id"] == second["_routine_thread_id"]
    assert second["data"]["two"] == 2
    assert [e["request_id"] for e in manager.events] == ["first", "first", "second", "second"]
    assert first["_routine_invocation"]["request_id"] == "first"
    assert second["_routine_invocation"]["request_id"] == "second"
    assert first["_routine_invocation"]["thread_id"] == first["_routine_thread_id"]


@pytest.mark.asyncio
async def test_lifecycle_reports_failure_instead_of_completion():
    doc = graph("failure", [{"id": "bad", "kind": "eval", "config": {"expression": "1 / 0"}}])
    manager = EventDocuments(doc)
    saver = MemorySaver()
    with pytest.raises(ZeroDivisionError):
        await executor(doc, manager, checkpointer=saver).ainvoke({"_routine_request_id": "failed"})
    assert [e["status"] for e in manager.events] == ["started", "failed"]
    assert manager.events[-1]["error"]
    checkpoint = await saver.aget_tuple(
        {"configurable": {"thread_id": manager.events[-1]["thread_id"]}}
    )
    assert checkpoint.checkpoint["channel_values"]["run_meta"]["bad"][0]["error"]


class ObjectAgent:
    def __init__(self):
        self.threads = []

    async def ainvoke(self, state, *, config):
        self.threads.append(config["configurable"]["thread_id"])
        await asyncio.sleep(0)
        return {"messages": [AIMessage(content="done")]}


@pytest.mark.asyncio
@pytest.mark.parametrize("retries", [0, 1])
async def test_timeout_persists_node_error_after_retries_and_keeps_failed_history(retries):
    class SlowAgent(ObjectAgent):
        async def ainvoke(self, state, *, config):
            self.threads.append(config["configurable"]["thread_id"])
            await asyncio.sleep(0.1)
            return {"messages": [AIMessage(content="done")]}

    delayed = delegate()
    delayed["config"].update(
        timeout=0.02, retry={"max_retries": retries, "initial_interval": 0, "jitter": False}
    )
    doc = graph(
        "timeout",
        [{"id": "first", "kind": "eval", "config": {"expression": "1"}}, delayed],
        [{"from": "first", "to": "delegate", "level": "normal"}],
    )
    manager, worker, saver = EventDocuments(doc), SlowAgent(), MemorySaver()
    run = executor(doc, manager, worker, checkpointer=saver)
    with pytest.raises(NodeTimeoutError) as caught:
        await run.ainvoke({"_routine_request_id": "timeout"})
    assert len(worker.threads) == retries + 1
    assert [event["status"] for event in manager.events] == ["started", "failed"]
    assert manager.events[-1]["error"] == str(caught.value)
    failed_config = {"configurable": {"thread_id": manager.events[-1]["thread_id"]}}
    checkpoint = await saver.aget_tuple(failed_config)
    history = checkpoint.checkpoint["channel_values"]["run_meta"]
    assert history["first"][0]["output"] == {"first": 1}
    assert len(history["delegate"]) == 1
    error = history["delegate"][0]
    assert error["step"] == 2
    assert error["error"] == str(caught.value)
    assert error["duration_ms"] == round(caught.value.elapsed * 1000, 2)
    assert error["started_at"]
    assert any(
        write[1] == "__error__" for cp in saver.list(failed_config) for write in cp.pending_writes
    )

    delayed["config"]["timeout"] = 0.5
    recovered = await run.ainvoke({"_routine_request_id": "recovered"})
    assert recovered["data"]["delegate"] == "done"
    assert recovered["_routine_thread_id"] != failed_config["configurable"]["thread_id"]
    old_checkpoint = await saver.aget_tuple(failed_config)
    assert old_checkpoint.checkpoint == checkpoint.checkpoint


def graph(routine_id, nodes, edges=(), **fields):
    nodes = [{"output_key": node["id"], **node} for node in nodes]
    return {
        "id": routine_id,
        "name": routine_id,
        "description": "execution regression",
        "version": "2.0.0",
        "entry": nodes[0]["id"],
        "nodes": nodes,
        "edges": list(edges),
        **fields,
    }


def delegate(node_id="delegate"):
    return {
        "id": node_id,
        "kind": "agent",
        "config": {"agent_name": "worker", "prompt_template": "{request}"},
    }


def executor(doc, manager, target=None, **kwargs):
    return create_routine_executor(
        routine_id=doc["id"],
        model=object(),
        state_manager=manager,
        revision_reader=NullRoutineRevisionReader(),
        agent_registry={"worker": target} if target else {},
        routine_registry={},
        **kwargs,
    )


@pytest.mark.asyncio
async def test_concurrent_runs_and_same_node_in_other_routine_have_distinct_threads():
    first, second = graph("first", [delegate()]), graph("second", [delegate()])
    manager, worker = Documents(first, second), ObjectAgent()
    one = executor(first, manager, worker)
    two = executor(second, manager, worker)
    results = await asyncio.gather(
        one.ainvoke({"messages": [HumanMessage("one")]}),
        one.ainvoke({"messages": [HumanMessage("two")]}),
        two.ainvoke({"messages": [HumanMessage("three")]}),
    )
    assert len(set(worker.threads)) == 3
    assert set(worker.threads) == {
        f"routine-agent:{result['_routine_thread_id']}:delegate" for result in results
    }


@pytest.mark.asyncio
async def test_loop_reuses_delegated_conversation_only_within_its_run():
    class LoopAgent(ObjectAgent):
        async def ainvoke(self, state, *, config):
            self.threads.append(config["configurable"]["thread_id"])
            again = len(self.threads) % 2 == 1
            return {"messages": [AIMessage(content='{"again": ' + str(again).lower() + "}")]}

    doc = graph(
        "loop",
        [delegate()],
        [
            {
                "from": "delegate",
                "to": "delegate",
                "level": "normal",
                "loop_guard": {"expression": "delegate_json.again"},
            }
        ],
    )
    worker = LoopAgent()
    run = executor(doc, Documents(doc), worker)
    await run.ainvoke({"messages": [HumanMessage("first")]})
    await run.ainvoke({"messages": [HumanMessage("second")]})
    assert worker.threads[0] == worker.threads[1]
    assert worker.threads[2] == worker.threads[3]
    assert worker.threads[0] != worker.threads[2]


@pytest.mark.asyncio
async def test_resume_preserves_run_correlation_and_context():
    doc = graph(
        "paused",
        [
            delegate(),
            {"id": "approve", "kind": "human", "config": {"message": "Proceed?"}},
            delegate("after"),
        ],
        [
            {"from": "delegate", "to": "approve", "level": "normal"},
            {"from": "approve", "to": "after", "level": "normal"},
        ],
    )
    manager, worker, saver = Documents(doc), ObjectAgent(), MemorySaver()
    run = executor(doc, manager, worker, checkpointer=saver, context={"workspace_id": "space"})
    paused = await run.ainvoke({"messages": [HumanMessage("go")]})
    thread_id = paused["_routine_thread_id"]
    assert paused["__interrupt__"]
    resumed = await resume_routine_thread(
        thread_id=thread_id,
        answer="yes",
        model=object(),
        state_manager=manager,
        revision_reader=NullRoutineRevisionReader(),
        agent_registry={"worker": worker},
        checkpointer=saver,
    )
    assert resumed["status"] == "completed"
    assert worker.threads == [
        f"routine-agent:{thread_id}:delegate",
        f"routine-agent:{thread_id}:after",
    ]
    checkpoint = await saver.aget_tuple({"configurable": {"thread_id": thread_id}})
    assert checkpoint.checkpoint["channel_values"]["data"]["context"] == {
        "workspace_id": "space",
        "request_id": thread_id,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("declared_schemas", [False, True])
async def test_nested_input_and_output_maps_survive_execution_adapter(declared_schemas):
    child = graph(
        "child", [{"id": "compute", "kind": "eval", "config": {"expression": "value + 1"}}]
    )
    if declared_schemas:
        child["input_schema"] = {"type": "object", "properties": {"value": {"type": "integer"}}}
        child["output_schema"] = {"type": "object", "properties": {"compute": {"type": "integer"}}}
    parent = graph(
        "parent",
        [
            {"id": "initial", "kind": "eval", "config": {"expression": "41"}},
            {
                "id": "nested",
                "kind": "routine",
                "config": {
                    "routine_name": "child",
                    "input_map": {"value": "initial"},
                    "output_map": {"answer": "compute"},
                },
            },
        ],
        [{"from": "initial", "to": "nested", "level": "normal"}],
    )
    manager = Documents(parent, child)

    async def run(routine_id, state, runtime=None):
        doc = await manager.get_routine_graph(routine_id)
        return await executor(
            doc, manager, routine_runner=run, context=dict(getattr(runtime, "context", None) or {})
        ).ainvoke(state)

    result = await run("parent", {"messages": [HumanMessage("calculate")]})
    assert result["data"]["answer"] == 42


@pytest.mark.asyncio
async def test_deleted_live_routine_does_not_fall_back_to_a_revision():
    class History(NullRoutineRevisionReader):
        async def fetch_latest(self, routine_id):
            raise AssertionError("An unpinned deleted routine must not be resurrected")

    spec = create_routine_subagent(
        routine_id="deleted",
        description="gone",
        model=object(),
        state_manager=Documents(),
        revision_reader=History(),
        agent_registry={},
        routine_registry={},
    )
    result = await spec["runnable"].ainvoke({"messages": []})
    assert "has no stored graph" in result["messages"][-1].content


@pytest.mark.asyncio
async def test_actual_agent_factory_runner_preserves_nested_data(monkeypatch):
    from src.progenitor.agent.factory import create_progenitor_agent
    from tests.progenitor.test_memory import FakeContentStateManager

    child = graph(
        "child",
        [
            {"id": "answer", "kind": "eval", "config": {"expression": "value + 1"}},
        ],
        input_schema={"type": "object", "properties": {"value": {"type": "integer"}}},
        output_schema={"type": "object", "properties": {"answer": {"type": "integer"}}},
    )
    parent = graph(
        "parent",
        [
            {"id": "initial", "kind": "eval", "config": {"expression": "41"}},
            {
                "id": "nested",
                "kind": "routine",
                "config": {
                    "routine_name": "child",
                    "input_map": {"value": "initial"},
                    "output_map": {"result": "answer"},
                },
            },
        ],
        [{"from": "initial", "to": "nested", "level": "normal"}],
    )

    class Store(FakeContentStateManager):
        async def get_execution_policy(self, policy_id):
            from src.progenitor.settings.policies import ExecutionPolicy

            return ExecutionPolicy(id=policy_id, name="Default").model_dump(mode="json")

        async def get_routine_graph(self, routine_id):
            return {"parent": parent, "child": child}.get(routine_id)

        async def list_routine_graphs(self):
            return [parent, child]

    agent = SimpleNamespace()
    monkeypatch.setattr("src.progenitor.agent.factory.create_deep_agent", lambda **_kwargs: agent)
    monkeypatch.setattr(
        "src.progenitor.agent.factory.create_progenitor_backend", lambda **_kwargs: object()
    )
    compiled = await create_progenitor_agent(state_manager=Store(), checkpointer=MemorySaver())
    result = await compiled.routine_runner("parent", {"messages": [HumanMessage("calculate")]})
    assert result["data"]["result"] == 42
    child_call = result["run_meta"]["nested"][0]["routine"]
    assert child_call["agent_id"] == "child"
    assert child_call["parent"] == {
        "thread_id": result["_routine_thread_id"],
        "node_id": "nested",
    }
    assert child_call["thread_id"].startswith("routine:child:")
    assert "_routine_invocation" not in result["data"]


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", ["approve", "decline"])
async def test_human_ui_answer_routes_once_on_original_checkpoint(answer):
    from unittest.mock import AsyncMock

    from src.progenitor.routines.approvals import pending_approvals
    from src.progenitor.routines.factory import compile_routine_graph
    from src.progenitor.routines.graph import validate_routine_graph_payload

    doc = graph(
        "approval",
        [
            {"id": "prepare", "kind": "eval", "config": {"expression": "41"}},
            {
                "id": "decision",
                "kind": "human",
                "config": {"message": "Continue?", "allowed_responses": ["approve", "decline"]},
            },
            {"id": "approved", "kind": "eval", "config": {"expression": "prepare + 1"}},
            {"id": "declined", "kind": "eval", "config": {"expression": "0"}},
        ],
        [
            {"from": "prepare", "to": "decision", "level": "normal"},
            {
                "from": "decision",
                "level": "route",
                "router": "state_equals",
                "state_key": "decision",
                "branches": {"approve": "approved", "decline": "declined"},
            },
        ],
    )
    manager, saver = EventDocuments(doc), MemorySaver()
    paused = await executor(doc, manager, checkpointer=saver).ainvoke({})
    thread = paused["_routine_thread_id"]
    approval = pending_approvals(paused["__interrupt__"])[0]
    kwargs = {
        "thread_id": thread,
        "model": object(),
        "state_manager": manager,
        "revision_reader": NullRoutineRevisionReader(),
        "agent_registry": {},
        "checkpointer": saver,
        "interrupt_id": approval["interrupt_id"],
        "request_id": "ui-answer",
    }
    with pytest.raises(ValueError, match="Response must"):
        await resume_routine_thread(answer="cancel", **kwargs)
    with pytest.raises(ValueError, match="no longer pending"):
        await resume_routine_thread(answer=answer, **{**kwargs, "interrupt_id": "stale"})
    manager.get_routine_approval_decision = AsyncMock(return_value={"request_id": "ui-answer"})
    with pytest.raises(ValueError, match="already been submitted"):
        await resume_routine_thread(answer=answer, **{**kwargs, "request_id": "chat-answer"})
    results = await asyncio.gather(
        resume_routine_thread(answer=answer, **kwargs),
        resume_routine_thread(answer=answer, **kwargs),
        return_exceptions=True,
    )
    assert sum(isinstance(item, ValueError) for item in results) == 1
    assert any(isinstance(item, dict) and item["status"] == "completed" for item in results)
    snapshot = await compile_routine_graph(
        validate_routine_graph_payload(doc), model=object(), checkpointer=saver
    ).aget_state({"configurable": {"thread_id": thread}})
    assert snapshot.interrupts == ()
    assert snapshot.values["data"]["decision"] == answer
    branch = "approved" if answer == "approve" else "declined"
    other = "declined" if answer == "approve" else "approved"
    assert snapshot.values["data"][branch] == (42 if answer == "approve" else 0)
    assert other not in snapshot.values["data"]
    assert len(snapshot.values["run_meta"]["prepare"]) == 1
    assert len(snapshot.values["run_meta"]["decision"]) == 1
    assert manager.events[-1]["thread_id"] == thread
    assert manager.events[-1]["request_id"] == "ui-answer"
