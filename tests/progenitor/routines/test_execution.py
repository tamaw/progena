"""Execution-boundary regressions: run isolation, resume and structured delegation."""

import asyncio
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver

from app.progenitor.routines.revisions import NullRoutineRevisionReader
from app.progenitor.routines.subagent import (
    create_routine_executor,
    create_routine_subagent,
    resume_routine_thread,
)


class Documents:
    def __init__(self, *graphs):
        self.graphs = {graph["id"]: graph for graph in graphs}

    async def get_routine_graph(self, routine_id):
        return self.graphs.get(routine_id)


class ObjectAgent:
    def __init__(self):
        self.threads = []

    async def ainvoke(self, state, *, config):
        self.threads.append(config["configurable"]["thread_id"])
        await asyncio.sleep(0)
        return {"messages": [AIMessage(content="done")]}


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
    from app.progenitor.agent.agent import create_progenitor_agent
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
        async def get_routine_graph(self, routine_id):
            return {"parent": parent, "child": child}.get(routine_id)

        async def list_routine_graphs(self):
            return [parent, child]

    agent = SimpleNamespace()
    monkeypatch.setattr(
        "app.progenitor.agent.agent.create_langchain_model", lambda _config: object()
    )
    monkeypatch.setattr("app.progenitor.agent.agent.create_deep_agent", lambda **_kwargs: agent)
    monkeypatch.setattr(
        "app.progenitor.agent.agent.create_progenitor_backend", lambda **_kwargs: object()
    )
    compiled = await create_progenitor_agent(state_manager=Store(), checkpointer=MemorySaver())
    result = await compiled.routine_runner("parent", {"messages": [HumanMessage("calculate")]})
    assert result["data"]["result"] == 42
