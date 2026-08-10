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


class AlwaysFailModel(FakeModel):
    async def ainvoke(self, messages: list[Any]) -> AIMessage:
        self.calls += 1
        raise RuntimeError("permanent")


class FakeObjectAgent:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def ainvoke(self, state: dict[str, Any]) -> dict[str, Any]:
        prompt = str(state["messages"][-1].content)
        self.prompts.append(prompt)
        return {"messages": [AIMessage(content=f"done:{prompt}")]}


def graph(*nodes: dict[str, Any], edges: list[dict[str, Any]] | None = None, **extra: Any):
    return validate_routine_graph_payload(
        {
            "id": "advanced",
            "name": "Advanced",
            "description": "feature coverage",
            "version": "2.0.0",
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
async def test_retry_exhaustion_routes_to_recovery_node() -> None:
    model = AlwaysFailModel()
    routine = graph(
        {
            "id": "work",
            "kind": "prompt",
            "output_key": "work",
            "config": {
                "prompt_template": "work",
                "retry": {
                    "max_attempts": 2,
                    "initial_interval": 0,
                    "jitter": False,
                    "on_exhausted": {"route_to": "recover"},
                },
            },
        },
        {
            "id": "recover",
            "kind": "eval",
            "output_key": "recover",
            "config": {"expression": "'recovered'"},
        },
    )
    result = await compile_routine_graph(routine, model=model).ainvoke(
        {"messages": [HumanMessage("go")]}
    )

    assert model.calls == 2
    assert result["data"]["recover"] == "recovered"
    assert result["data"]["errors"]["work"] == {
        "type": "RuntimeError",
        "message": "permanent",
    }


@pytest.mark.asyncio
async def test_router_branch_join_via_deferred_fan_in() -> None:
    """A fan_in join's declared sources are router branches, which are
    mutually exclusive -- at most one of them ever runs. The join must
    still fire once the branch that *did* run has finished, rather than
    waiting forever for the untaken branch (see compile_routine_graph's
    fan_in_targets/defer wiring)."""
    routine = graph(
        {"id": "gate", "kind": "eval", "output_key": "gate", "config": {"expression": "True"}},
        {
            "id": "left",
            "kind": "eval",
            "output_key": "left",
            "config": {"expression": "'L'"},
        },
        {
            "id": "right",
            "kind": "eval",
            "output_key": "right",
            "config": {"expression": "'R'"},
        },
        {
            "id": "join",
            "kind": "eval",
            "output_key": "join",
            "config": {"expression": "'joined'"},
        },
        edges=[
            {
                "from": "gate",
                "level": "route",
                "router": "state_equals",
                "state_key": "gate",
                "branches": {"True": "left", "False": "right"},
            },
            {"from": ["left", "right"], "to": "join", "level": "fan_in"},
        ],
    )
    result = await compile_routine_graph(routine, model=FakeModel()).ainvoke(
        {"messages": [HumanMessage("go")]}
    )

    assert result["data"]["left"] == "L"
    assert result["data"]["join"] == "joined"
    assert "right" not in result["data"]


@pytest.mark.asyncio
async def test_loop_guard_routes_false_result_without_running_guarded_target() -> None:
    routine = graph(
        {"id": "start", "kind": "noop", "output_key": "start", "config": {}},
        {
            "id": "loop",
            "kind": "eval",
            "output_key": "loop",
            "config": {"expression": "'unexpected'"},
        },
        {
            "id": "fallback",
            "kind": "eval",
            "output_key": "fallback",
            "config": {"expression": "'finished'"},
        },
        edges=[
            {
                "from": "start",
                "to": "loop",
                "level": "normal",
                "loop_guard": {
                    "expression": "count < 2",
                    "inputs": {"count": "count"},
                    "on_false": "fallback",
                },
            }
        ],
    )
    result = await compile_routine_graph(routine, model=FakeModel()).ainvoke(
        {"messages": [HumanMessage("go")], "data": {"count": 2}}
    )

    assert result["data"]["fallback"] == "finished"
    assert "loop" not in result["data"]


@pytest.mark.asyncio
async def test_foreach_agent_caps_items_and_collects_ordered_results() -> None:
    worker = FakeObjectAgent()
    routine = graph(
        {
            "id": "fan",
            "kind": "foreach",
            "output_key": "results",
            "config": {
                "mode": "agent",
                "items_from": "items",
                "agent_name": "worker",
                "prompt_template": "Review {item}",
                "max_items": 2,
                "max_concurrency": 1,
            },
        },
    )
    result = await compile_routine_graph(
        routine, model=FakeModel(), agent_registry={"worker": worker}
    ).ainvoke({"messages": [HumanMessage("go")], "data": {"items": ["a", "b", "c"]}})

    assert worker.prompts == ["Review a", "Review b"]
    assert result["data"]["results"] == ["done:Review a", "done:Review b"]


@pytest.mark.asyncio
async def test_human_interrupt_can_resume_with_checkpoint() -> None:
    routine = graph(
        {
            "id": "approve",
            "kind": "human",
            "output_key": "approved",
            "config": {"message": "Approve?", "allowed_responses": ["yes", "no"]},
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
            "kind": "eval",
            "output_key": "child",
            "config": {"expression": "item + 1", "inputs": {"item": "item"}},
        },
    )
    parent = graph(
        {
            "id": "fan",
            "kind": "foreach",
            "output_key": "fan",
            "config": {"mode": "send", "items_from": "items", "item_key": "item"},
        },
        {
            "id": "child",
            "kind": "routine",
            "output_key": "result",
            "config": {
                "routine_name": "child",
                "output_map": {"child_result": "child"},
            },
        },
        edges=[{"from": "fan", "to": "child", "level": "normal"}],
    )
    child_compiled = compile_routine_graph(child, model=FakeModel())
    parent_compiled = compile_routine_graph(
        parent, model=FakeModel(), routine_registry={"child": child_compiled}
    )
    result = await parent_compiled.ainvoke(
        {"messages": [HumanMessage("go")], "data": {"items": [1, 2]}}
    )
    assert result["data"]["fan"] == [1, 2]
    assert result["data"]["child_result"] in (2, 3)


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
