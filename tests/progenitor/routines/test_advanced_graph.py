from __future__ import annotations

from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from app.progenitor.routines.factory import compile_routine_graph
from app.progenitor.routines.graph import validate_routine_graph_payload


class FakeModel:
    def __init__(self) -> None:
        self.calls = 0

    async def ainvoke(self, messages: list[Any]) -> AIMessage:
        self.calls += 1
        return AIMessage(content=str(messages[-1].content))


class FlakyModel(FakeModel):
    async def ainvoke(self, messages: list[Any]) -> AIMessage:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("transient")
        return AIMessage(content=str(messages[-1].content))


def graph(*nodes: dict[str, Any], edges: list[dict[str, Any]] | None = None, **extra: Any):
    return validate_routine_graph_payload(
        {
            "id": "advanced",
            "name": "Advanced",
            "description": "feature coverage",
            "version": "1.0.0",
            "entry": nodes[0]["id"],
            "nodes": list(nodes),
            "edges": edges or [],
            **extra,
        }
    )


@pytest.mark.asyncio
async def test_runtime_context_and_stream_events() -> None:
    model = FakeModel()
    routine = graph(
        {
            "id": "say",
            "kind": "prompt",
            "output_key": "say",
            "config": {"prompt_template": "hello {context[user_id]}"},
        },
    )
    compiled = compile_routine_graph(routine, model=model)
    result = await compiled.ainvoke({"messages": [HumanMessage("go")]}, context={"user_id": "ada"})
    assert result["data"]["say"] == "hello ada"
    events = [
        event
        async for event in compiled.astream_events(
            {"messages": [HumanMessage("go")]}, version="v2", context={"user_id": "ada"}
        )
    ]
    assert any(event["event"] == "on_chain_start" and event["name"] == "say" for event in events)


@pytest.mark.asyncio
async def test_retry_policy_retries_transient_node_failure() -> None:
    model = FlakyModel()
    routine = graph(
        {
            "id": "say",
            "kind": "prompt",
            "output_key": "say",
            "config": {
                "prompt_template": "ok",
                "retry": {"max_attempts": 2, "initial_interval": 0, "jitter": False},
            },
        },
    )
    compiled = compile_routine_graph(routine, model=model)
    assert (await compiled.ainvoke({"messages": [HumanMessage("go")]}))["data"]["say"] == "ok"
    assert model.calls == 2


@pytest.mark.asyncio
async def test_human_interrupt_can_resume_with_checkpoint() -> None:
    routine = graph(
        {
            "id": "approve",
            "kind": "human",
            "output_key": "approved",
            "config": {"prompt": "Approve?"},
        },
    )
    compiled = compile_routine_graph(routine, model=FakeModel(), checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "advanced-human"}}
    paused = await compiled.ainvoke({"messages": [HumanMessage("go")]}, config=config)
    assert paused is not None
    resumed = await compiled.ainvoke(Command(resume="yes"), config=config)
    assert resumed["data"]["approved"] == "yes"


@pytest.mark.asyncio
async def test_send_fanout_and_native_subgraph() -> None:
    child = graph(
        {
            "id": "child",
            "kind": "code",
            "output_key": "child",
            "config": {"expression": "item + 1", "inputs": {"item": "item"}},
        },
    )
    parent = graph(
        {
            "id": "fan",
            "kind": "foreach",
            "output_key": "fan",
            "config": {"items_path": "items", "target": "child", "item_key": "item"},
        },
        {
            "id": "child",
            "kind": "subgraph",
            "output_key": "result",
            "config": {"subgraph_name": "child"},
        },
        edges=[{"from": "fan", "to": "child"}],
    )
    child_compiled = compile_routine_graph(child, model=FakeModel())
    parent_compiled = compile_routine_graph(
        parent, model=FakeModel(), subgraph_registry={"child": child_compiled}
    )
    result = await parent_compiled.ainvoke(
        {"messages": [HumanMessage("go")], "data": {"items": [1, 2]}}
    )
    assert result["data"]["item"] in (1, 2)


@pytest.mark.asyncio
async def test_node_cache_is_enabled_by_graph_config() -> None:
    model = FakeModel()
    routine = graph(
        {
            "id": "say",
            "kind": "prompt",
            "output_key": "say",
            "config": {"prompt_template": "cached", "cache": {}},
        },
        settings={"cache": True},
    )
    compiled = compile_routine_graph(routine, model=model)
    await compiled.ainvoke({"messages": [HumanMessage("go")]})
    await compiled.ainvoke({"messages": [HumanMessage("go")]})
    assert model.calls == 1
