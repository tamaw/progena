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
                "retry": {"max_retries": 1, "initial_interval": 0, "jitter": False},
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
                    "max_retries": 1,
                    "initial_interval": 0,
                    "jitter": False,
                },
            },
        },
        {
            "id": "recover",
            "kind": "eval",
            "output_key": "recover",
            "config": {"expression": "'recovered'"},
        },
        edges=[
            {"from": "work", "to": "recover", "level": "error"},
        ],
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
async def test_no_error_edge_still_propagates_exception() -> None:
    """A node without an error edge still bubbles the exception up rather than
    swallowing it silently."""
    model = AlwaysFailModel()
    routine = graph(
        {
            "id": "work",
            "kind": "prompt",
            "output_key": "work",
            "config": {
                "prompt_template": "work",
                "retry": {
                    "max_retries": 0,
                    "initial_interval": 0,
                    "jitter": False,
                },
            },
        },
    )
    compiled = compile_routine_graph(routine, model=model)
    with pytest.raises(RuntimeError, match="permanent"):
        await compiled.ainvoke({"messages": [HumanMessage("go")]})


def test_settings_timeout_default_is_120() -> None:
    """When settings.timeout is not declared, the default 120 s is wired to
    LangGraph's add_node."""
    routine = graph(
        {
            "id": "a",
            "kind": "prompt",
            "output_key": "a",
            "config": {"prompt_template": "hi"},
        },
    )
    compiled = compile_routine_graph(routine, model=FakeModel())
    assert compiled.nodes["a"].timeout.run_timeout == 120.0


def test_per_node_timeout_overrides_settings() -> None:
    """A node's own config.timeout wins over settings.timeout."""
    routine = graph(
        {
            "id": "a",
            "kind": "prompt",
            "output_key": "a",
            "config": {"prompt_template": "hi", "timeout": 30},
        },
        settings={"timeout": 60},
    )
    compiled = compile_routine_graph(routine, model=FakeModel())
    assert compiled.nodes["a"].timeout.run_timeout == 30.0


def test_timeout_zero_means_no_limit() -> None:
    """timeout: 0 disables the timeout limit entirely."""
    routine = graph(
        {
            "id": "a",
            "kind": "prompt",
            "output_key": "a",
            "config": {"prompt_template": "hi", "timeout": 0},
        },
    )
    compiled = compile_routine_graph(routine, model=FakeModel())
    assert compiled.nodes["a"].timeout is None


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
                "branches": {"true": "left", "false": "right"},
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


@pytest.mark.parametrize(
    ("expression", "taken", "untaken"),
    [("1 == 1", "yes", "no"), ("1 == 2", "no", "yes")],
)
@pytest.mark.asyncio
async def test_condition_routes_on_json_cased_boolean_branches(
    expression: str, taken: str, untaken: str
) -> None:
    """A condition coerces its expression to a bool, and a routine document is
    JSON -- so its two arms are keyed `true`/`false`, not Python's `str(True)`
    -> `"True"`. Regression guard for the router silently finding no branch."""
    routine = graph(
        {
            "id": "gate",
            "kind": "condition",
            "output_key": "gate",
            "config": {"expression": expression},
        },
        {"id": "yes", "kind": "eval", "output_key": "yes", "config": {"expression": "'Y'"}},
        {"id": "no", "kind": "eval", "output_key": "no", "config": {"expression": "'N'"}},
        edges=[
            {
                "from": "gate",
                "level": "route",
                "router": "state_equals",
                "state_key": "gate",
                "branches": {"true": "yes", "false": "no"},
            },
        ],
    )
    result = await compile_routine_graph(routine, model=FakeModel()).ainvoke(
        {"messages": [HumanMessage("go")]}
    )

    assert taken in result["data"]
    assert untaken not in result["data"]


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
async def test_eval_node_with_defaults_increments_from_zero_and_drives_a_loop_guard() -> None:
    """The pattern AgentGraph.jsx's `insertAt` auto-wires for a fresh Loop:
    start -> tail(eval, self-incrementing) -> (guard) -> start, exiting once
    the guard's own bound is reached. There is no dedicated counter node --
    `eval`'s `config.defaults` lets its own top-level name resolution fall
    back to 0 instead of raising, specifically so the same expression node
    can read its own not-yet-written prior output on its first visit."""
    routine = graph(
        {"id": "start", "kind": "noop", "output_key": "start", "config": {}},
        {
            "id": "tail",
            "kind": "eval",
            "output_key": "tail",
            "config": {
                "expression": "tail + 1",
                "defaults": {"tail": 0},
            },
        },
        {"id": "after", "kind": "eval", "output_key": "after", "config": {"expression": "'done'"}},
        edges=[
            {"from": "start", "to": "tail", "level": "normal"},
            {
                "from": "tail",
                "to": "start",
                "level": "normal",
                "loop_guard": {
                    "expression": "tail < 3",
                    "on_false": "after",
                },
            },
        ],
    )
    result = await compile_routine_graph(routine, model=FakeModel()).ainvoke(
        {"messages": [HumanMessage("go")], "data": {}}
    )

    assert result["data"]["tail"] == 3
    assert result["data"]["after"] == "done"


@pytest.mark.asyncio
async def test_eval_node_default_only_applies_when_the_input_path_is_missing() -> None:
    """The fallback in `config.defaults` must not mask a genuinely present
    value -- a plain `{**defaults, **data}` merge always lets the real
    `data` value win on a shared key, defaults only fills in what's
    actually missing."""
    routine = graph(
        {
            "id": "hits",
            "kind": "eval",
            "output_key": "hits",
            "config": {
                "expression": "hits + 1",
                "defaults": {"hits": 0},
            },
        },
    )
    result = await compile_routine_graph(routine, model=FakeModel()).ainvoke(
        {"messages": [HumanMessage("go")], "data": {}}
    )

    assert result["data"]["hits"] == 1

    result = await compile_routine_graph(routine, model=FakeModel()).ainvoke(
        {"messages": [HumanMessage("go")], "data": {"hits": 5}}
    )

    assert result["data"]["hits"] == 6


def test_eval_condition_switch_and_loop_guard_reject_a_legacy_inputs_field() -> None:
    """`inputs` used to be the only way an eval/condition/switch expression or
    a loop_guard could reach state -- now expressions reference state
    directly (bare Name, or `a.b` for a nested dict), so `inputs` is a
    rejected, unrecognized property on all four rather than a silently
    ignored one."""
    for config in (
        {"expression": "x", "inputs": {"x": "x"}},
        {"language": "python", "expression": "x", "inputs": {"x": "x"}},
    ):
        for kind in ("eval", "condition", "switch"):
            with pytest.raises(ValueError, match="schema validation failed"):
                graph({"id": "a", "kind": kind, "output_key": "a", "config": config})

    with pytest.raises(ValueError, match="schema validation failed"):
        graph(
            {"id": "a", "kind": "noop", "output_key": "a", "config": {}},
            {"id": "b", "kind": "noop", "output_key": "b", "config": {}},
            edges=[
                {
                    "from": "a",
                    "to": "b",
                    "level": "normal",
                    "loop_guard": {"expression": "x", "inputs": {"x": "x"}},
                }
            ],
        )


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
            "config": {"expression": "item + 1"},
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
