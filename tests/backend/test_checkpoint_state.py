"""Checkpoint-only state reconstruction regression tests."""

from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send, interrupt
from typing_extensions import TypedDict

from src.backend.checkpoint_state import checkpoint_state


class _State(TypedDict):
    value: int


async def _summary(graph, saver: InMemorySaver, config: dict) -> tuple[dict, tuple[str, ...]]:
    saved = await saver.aget_tuple(config)
    return checkpoint_state(saved), (await graph.aget_state(config)).next


@pytest.mark.parametrize("branch", ["left", "right"])
async def test_checkpoint_state_matches_real_stategraph_pull_tasks(branch: str) -> None:
    async def step(state: _State) -> dict[str, int]:
        return {"value": state["value"] + 1}

    async def finish(state: _State) -> dict[str, int]:
        return {"value": state["value"]}

    def choose(state: _State) -> str:
        return branch

    saver = InMemorySaver()
    graph_builder = StateGraph(_State)
    graph_builder.add_node("step", step)
    graph_builder.add_node("left", finish)
    graph_builder.add_node("right", finish)
    graph_builder.add_edge(START, "step")
    graph_builder.add_conditional_edges("step", choose, {"left": "left", "right": "right"})
    graph_builder.add_edge("left", END)
    graph_builder.add_edge("right", END)
    graph = graph_builder.compile(checkpointer=saver, interrupt_before=[branch])
    config = {
        "configurable": {"thread_id": f"conditional-{branch}"},
        "metadata": {"runtime_instance": "runtime-a"},
    }

    await graph.ainvoke({"value": 0}, config)
    state, expected_next = await _summary(graph, saver, config)

    assert state["next"] == list(expected_next) == [branch]
    assert state["waiting"] is False
    assert state["failed"] is False
    assert state["runtime_instance"] == "runtime-a"


async def test_checkpoint_state_matches_send_and_waiting_tasks() -> None:
    async def route(state: _State) -> dict[str, int]:
        return {}

    def send_to_worker(state: _State) -> list[Send]:
        return [Send("worker", {"value": state["value"]})]

    async def worker(state: _State) -> dict[str, int]:
        return {"value": state["value"] + 1}

    saver = InMemorySaver()
    graph_builder = StateGraph(_State)
    graph_builder.add_node("route", route)
    graph_builder.add_node("worker", worker)
    graph_builder.add_edge(START, "route")
    graph_builder.add_conditional_edges("route", send_to_worker)
    graph_builder.add_edge("worker", END)
    graph = graph_builder.compile(checkpointer=saver, interrupt_before=["worker"])
    config = {"configurable": {"thread_id": "send"}}

    await graph.ainvoke({"value": 0}, config)
    state, expected_next = await _summary(graph, saver, config)

    assert state["next"] == list(expected_next) == ["worker"]
    assert state["waiting"] is False
    assert state["failed"] is False

    async def wait_for_person(state: _State) -> dict[str, int]:
        interrupt("approval")
        return {"value": state["value"]}

    waiting_saver = InMemorySaver()
    waiting_builder = StateGraph(_State)
    waiting_builder.add_node("wait", wait_for_person)
    waiting_builder.add_edge(START, "wait")
    waiting_builder.add_edge("wait", END)
    waiting_graph = waiting_builder.compile(checkpointer=waiting_saver)
    waiting_config = {"configurable": {"thread_id": "waiting"}}

    await waiting_graph.ainvoke({"value": 0}, waiting_config)
    waiting, expected_next = await _summary(waiting_graph, waiting_saver, waiting_config)

    assert waiting["next"] == list(expected_next) == ["wait"]
    assert waiting["waiting"] is True
    assert waiting["failed"] is False


async def test_checkpoint_state_marks_a_failed_stopped_turn() -> None:
    async def fail(state: _State) -> dict[str, int]:
        raise ValueError("401")

    saver = InMemorySaver()
    graph_builder = StateGraph(_State)
    graph_builder.add_node("fail", fail)
    graph_builder.add_edge(START, "fail")
    graph_builder.add_edge("fail", END)
    graph = graph_builder.compile(checkpointer=saver)
    config = {"configurable": {"thread_id": "failed"}}

    with pytest.raises(ValueError, match="401"):
        await graph.ainvoke({"value": 0}, config)
    state, expected_next = await _summary(graph, saver, config)

    assert state["next"] == list(expected_next) == ["fail"]
    assert state["waiting"] is False
    assert state["failed"] is True
