from __future__ import annotations

from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from src.progenitor.routines.factory import compile_routine_graph
from src.progenitor.routines.graph import validate_routine_graph_payload
from src.progenitor.settings.policies import PolicyValues


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
                "retry": {
                    "retry_on": "all",
                    "max_retries": 1,
                    "initial_interval": 0,
                    "jitter": False,
                },
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
                    "retry_on": "all",
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
                    "retry_on": "all",
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
    )
    compiled = compile_routine_graph(
        routine, model=FakeModel(), execution_policy=PolicyValues(attempt_timeout=60)
    )
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
async def test_run_meta_step_orders_execution_across_the_whole_run() -> None:
    """Each run_meta entry's `step` is a 1-based ordinal across every node
    execution so far this run -- lets the UI place a given execution among
    every other node's, not just within its own entry list (see
    _instrument_node_callable)."""
    routine = graph(
        {"id": "a", "kind": "eval", "output_key": "a", "config": {"expression": "1"}},
        {"id": "b", "kind": "eval", "output_key": "b", "config": {"expression": "2"}},
        {"id": "c", "kind": "eval", "output_key": "c", "config": {"expression": "3"}},
        edges=[
            {"from": "a", "to": "b", "level": "normal"},
            {"from": "b", "to": "c", "level": "normal"},
        ],
    )
    result = await compile_routine_graph(routine, model=FakeModel()).ainvoke(
        {"messages": [HumanMessage("go")]}
    )

    run_meta = result["run_meta"]
    assert run_meta["a"][0]["step"] == 1
    assert run_meta["b"][0]["step"] == 2
    assert run_meta["c"][0]["step"] == 3


@pytest.mark.parametrize(
    ("expression", "taken"),
    [("1 == 1", "yes"), ("1 == 2", "no")],
)
@pytest.mark.asyncio
async def test_run_meta_records_route_taken_for_a_router_edge(expression: str, taken: str) -> None:
    """A router's outcome (which branch was actually taken) is recorded on
    the routing node's own run_meta entry -- re-derived from the exact same
    decision function LangGraph itself uses (see route_fns/route_fn in
    compile_routine_graph), not a separate guess."""
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

    assert result["run_meta"]["gate"][0]["route"] == taken


@pytest.mark.asyncio
async def test_run_meta_records_route_taken_for_a_loop_guard_edge() -> None:
    """A loop_guard's outcome is recorded the same way -- the looped-back
    target while the guard holds, the exit target on the iteration it
    finally fails. Each iteration's own `output` is preserved too, not just
    the final cumulative `data` value -- `tail`'s expression increments
    itself, so a naive "read `data['tail']` per iteration" would show 2 for
    every entry instead of each iteration's own real value."""
    routine = graph(
        {"id": "start", "kind": "noop", "output_key": "start", "config": {}},
        {
            "id": "tail",
            "kind": "eval",
            "output_key": "tail",
            "config": {"expression": "tail + 1", "defaults": {"tail": 0}},
        },
        {"id": "after", "kind": "eval", "output_key": "after", "config": {"expression": "'done'"}},
        edges=[
            {"from": "start", "to": "tail", "level": "normal"},
            {
                "from": "tail",
                "to": "start",
                "level": "normal",
                "loop_guard": {"expression": "tail < 2", "on_false": "after"},
            },
        ],
    )
    result = await compile_routine_graph(routine, model=FakeModel()).ainvoke(
        {"messages": [HumanMessage("go")], "data": {}}
    )

    tail_entries = result["run_meta"]["tail"]
    assert [entry["route"] for entry in tail_entries] == ["start", "after"]
    assert [entry["output"] for entry in tail_entries] == [{"tail": 1}, {"tail": 2}]


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
async def test_send_fanout_empty_routes_through_join_marker() -> None:
    routine = graph(
        {
            "id": "fan",
            "kind": "foreach",
            "output_key": "items_out",
            "config": {"mode": "send", "items_from": "items", "item_key": "item"},
        },
        {
            "id": "body",
            "kind": "eval",
            "output_key": "doubled",
            "config": {"expression": "item * 2"},
        },
        {"id": "join_each", "kind": "noop", "output_key": "join_each"},
        {
            "id": "summary",
            "kind": "eval",
            "output_key": "summary",
            "config": {"expression": "items"},
        },
        edges=[
            {"from": "fan", "to": "body", "level": "normal"},
            {"from": "body", "to": "join_each", "level": "normal"},
            {"from": "join_each", "to": "summary", "level": "normal"},
            {"from": "fan", "to": "join_each", "level": "foreach_empty"},
        ],
    )
    compiled = compile_routine_graph(routine, model=FakeModel())

    empty = await compiled.ainvoke({"messages": [HumanMessage("go")], "data": {"items": []}})
    assert empty["data"]["summary"] == []
    assert len(empty["run_meta"]["join_each"]) == 1

    nonempty = await compiled.ainvoke(
        {"messages": [HumanMessage("go")], "data": {"items": [2, 3, 4]}}
    )
    assert nonempty["data"]["summary"] == [2, 3, 4]
    assert len(nonempty["run_meta"]["body"]) == 3
    assert len(nonempty["run_meta"]["join_each"]) == 1


@pytest.mark.asyncio
async def test_reduce_collects_send_body_outputs_and_handles_empty_input() -> None:
    routine = graph(
        {
            "id": "fan",
            "kind": "foreach",
            "output_key": "fan",
            "config": {"mode": "send", "items_from": "items", "item_key": "item"},
        },
        {
            "id": "mapped",
            "kind": "eval",
            "output_key": "mapped",
            "config": {"expression": "item * 2"},
        },
        {"id": "join_each", "kind": "noop", "output_key": "join_each"},
        {
            "id": "sum",
            "kind": "reduce",
            "output_key": "sum",
            "config": {"source_node": "mapped", "operation": "sum"},
        },
        {
            "id": "collected",
            "kind": "reduce",
            "output_key": "collected",
            "config": {"source_node": "mapped", "operation": "collect"},
        },
        {
            "id": "product",
            "kind": "reduce",
            "output_key": "product",
            "config": {"source_node": "mapped", "operation": "product"},
        },
        {
            "id": "count",
            "kind": "reduce",
            "output_key": "count",
            "config": {"source_node": "mapped", "operation": "count"},
        },
        {
            "id": "minimum",
            "kind": "reduce",
            "output_key": "minimum",
            "config": {"source_node": "mapped", "operation": "min"},
        },
        {
            "id": "maximum",
            "kind": "reduce",
            "output_key": "maximum",
            "config": {"source_node": "mapped", "operation": "max"},
        },
        edges=[
            {"from": "fan", "to": "mapped", "level": "normal"},
            {"from": "mapped", "to": "join_each", "level": "normal"},
            {"from": "join_each", "to": "sum", "level": "normal"},
            {"from": "fan", "to": "join_each", "level": "foreach_empty"},
            {"from": "sum", "to": "product", "level": "normal"},
            {"from": "product", "to": "count", "level": "normal"},
            {"from": "count", "to": "minimum", "level": "normal"},
            {"from": "minimum", "to": "maximum", "level": "normal"},
            {"from": "maximum", "to": "collected", "level": "normal"},
        ],
    )
    compiled = compile_routine_graph(routine, model=FakeModel())

    mapped = await compiled.ainvoke(
        {"messages": [HumanMessage("go")], "data": {"items": [2, 3, 4]}}
    )
    assert mapped["data"]["sum"] == 18
    assert mapped["data"]["collected"] == [4, 6, 8]
    assert mapped["data"]["product"] == 192
    assert mapped["data"]["count"] == 3
    assert mapped["data"]["minimum"] == 4
    assert mapped["data"]["maximum"] == 8
    assert mapped["node_outputs"]["mapped"] == [4, 6, 8]
    assert len(mapped["run_meta"]["mapped"]) == 3
    assert len(mapped["run_meta"]["sum"]) == 1

    empty = await compiled.ainvoke({"messages": [HumanMessage("go")], "data": {"items": []}})
    assert empty["data"]["sum"] == 0
    assert empty["data"]["collected"] == []
    assert empty["data"]["product"] == 1
    assert empty["data"]["count"] == 0
    assert empty["data"]["minimum"] is None
    assert empty["data"]["maximum"] is None
    assert "mapped" not in empty["run_meta"]


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
    )
    compiled = compile_routine_graph(routine, model=model)
    await compiled.ainvoke({"messages": [HumanMessage("go")]})
    await compiled.ainvoke({"messages": [HumanMessage("go")]})
    assert model.calls == 1


@pytest.mark.parametrize("continue_thread", [True, False])
@pytest.mark.parametrize("context", [{"request_id": "request-42"}, {}])
async def test_object_delegation_preserves_thread_config(continue_thread, context) -> None:
    calls = []

    class ObjectAgent:
        async def ainvoke(self, state, **kwargs):
            calls.append((state, kwargs))
            return {"messages": [AIMessage(content="done")]}

    routine = graph(
        {
            "id": "delegate",
            "kind": "agent",
            "output_key": "delegate",
            "config": {
                "agent_name": "worker",
                "prompt_template": "{request}",
                "continue_thread": continue_thread,
            },
        }
    )
    compiled = compile_routine_graph(
        routine, model=FakeModel(), agent_registry={"worker": ObjectAgent()}
    )
    for request in ("first", "second"):
        result = await compiled.ainvoke({"messages": [HumanMessage(request)]}, context=context)
        assert result["data"]["delegate"] == "done"

    expected_kwargs = (
        {
            "config": {
                "configurable": {
                    "thread_id": f"routine-agent:{context.get('request_id', 'routine')}:delegate"
                }
            }
        }
        if continue_thread
        else {}
    )
    assert [state["messages"][0].content for state, _ in calls] == ["first", "second"]
    assert [kwargs for _, kwargs in calls] == [expected_kwargs, expected_kwargs]


async def test_nested_routine_runner_receives_runtime_and_mapped_state() -> None:
    calls = []

    async def runner(name, state, runtime):
        calls.append((name, state, runtime.context))
        return {"messages": [AIMessage(content="child done")], "data": {"answer": 42}}

    routine = graph(
        {
            "id": "child",
            "kind": "routine",
            "output_key": "child",
            "config": {
                "routine_name": "nested",
                "input_map": {"question": "request"},
                "output_map": {"mapped_answer": "answer"},
            },
        }
    )
    compiled = compile_routine_graph(routine, model=FakeModel(), routine_runner=runner)
    context = {"request_id": "parent-42"}
    result = await compiled.ainvoke(
        {"messages": [HumanMessage("what is the answer?")]}, context=context
    )
    assert len(calls) == 1
    name, child_state, child_context = calls[0]
    assert name == "nested"
    assert child_state["data"] == {"question": "what is the answer?"}
    assert child_state["messages"][0].content == "what is the answer?"
    assert child_context == context
    assert result["data"]["mapped_answer"] == 42
    assert result["data"]["child"] == "child done"


@pytest.mark.asyncio
async def test_nested_history_preserves_each_call_revision_and_location() -> None:
    records = []

    async def runner(name, state, runtime):
        invocation = {
            "agent_id": name,
            "revision_id": f"revision-{len(records)}",
            "request_id": f"invocation-{len(records)}",
            "parent": state["_routine_parent"],
            "thread_id": f"child-thread-{len(records)}",
        }
        records.append(invocation)
        return {"data": {}, "_routine_invocation": invocation}

    routine = graph(
        {
            "id": "first",
            "output_key": "first",
            "kind": "routine",
            "config": {"routine_name": "child"},
        },
        {
            "id": "second",
            "output_key": "second",
            "kind": "routine",
            "config": {"routine_name": "child"},
        },
        edges=[{"from": "first", "to": "second", "level": "normal"}],
    )
    saver = MemorySaver()
    compiled = compile_routine_graph(
        routine, model=FakeModel(), routine_runner=runner, checkpointer=saver
    )
    config = {"configurable": {"thread_id": "parent"}}
    await compiled.ainvoke({"messages": []}, config=config, context={"request_id": "parent"})
    snapshot = await compiled.aget_state(config)
    for index, node_id in enumerate(("first", "second")):
        assert snapshot.values["run_meta"][node_id][0]["routine"] == records[index]
        assert records[index]["parent"] == {"thread_id": "parent", "node_id": node_id}
    assert "_routine_invocation" not in snapshot.values


@pytest.mark.asyncio
async def test_failed_nested_call_retains_resolved_revision_metadata() -> None:
    invocation = {"agent_id": "child", "revision_id": "failed-revision"}

    async def runner(name, state, runtime):
        error = ValueError("child failed")
        error.routine_invocation = invocation
        raise error

    compiled = compile_routine_graph(
        graph(
            {
                "id": "call",
                "output_key": "call",
                "kind": "routine",
                "config": {"routine_name": "child"},
            }
        ),
        model=FakeModel(),
        routine_runner=runner,
    )
    with pytest.raises(ValueError) as caught:
        await compiled.ainvoke({"messages": []})
    node_id, record = caught.value.routine_run_meta
    assert node_id == "call"
    assert record["routine"] == invocation
    assert record["error"] == "child failed"
