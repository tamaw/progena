"""Tests for the routine CompiledSubAgent proxy (app.progenitor.routines.subagent)."""

from __future__ import annotations

from typing import Any

import pytest
from deepagents import create_deep_agent
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage

from app.progenitor.agent.state import ProgenitorAgentState
from app.progenitor.routines.subagent import (
    ROUTINE_REVISIONS_STATE_KEY,
    create_routine_subagent,
)

ROUTINE_ID = "routine-demo"


def _graph_doc(text: str) -> dict[str, Any]:
    """A minimal one-node routine graph whose prompt output is ``text``."""
    return {
        "id": ROUTINE_ID,
        "name": "Routine Demo",
        "description": "test routine",
        "version": "2.0.0",
        "entry": "say",
        "nodes": [
            {
                "id": "say",
                "kind": "prompt",
                "output_key": "out",
                "config": {"prompt_template": text},
            }
        ],
        "edges": [],
    }


class _EchoModel:
    """Fake chat model that echoes the rendered prompt text back verbatim."""

    def __init__(self) -> None:
        self.calls: list[list] = []

    async def ainvoke(self, messages, *args, **kwargs):
        self.calls.append(messages)
        return AIMessage(content=messages[-1].content)


class _FakeRevisionReader:
    """Fake RoutineRevisionReader over an in-memory revision map."""

    def __init__(self, revisions: dict[str, dict[str, Any]], *, latest: str | None = None):
        self._revisions = revisions
        self._latest = latest
        self.fetch_calls: list[tuple[str, str]] = []
        self.fetch_latest_calls: list[str] = []

    async def fetch_latest(self, routine_id: str) -> tuple[str, dict[str, Any]] | None:
        self.fetch_latest_calls.append(routine_id)
        if self._latest is None:
            return None
        return self._latest, self._revisions[self._latest]

    async def fetch(self, routine_id: str, revision_id: str) -> dict[str, Any] | None:
        self.fetch_calls.append((routine_id, revision_id))
        return self._revisions.get(revision_id)


class _FakeStateManager:
    def __init__(self, graph_doc: dict[str, Any] | None = None):
        self._graph_doc = graph_doc
        self.calls = 0


def _spec(reader, state_manager=None, model=None, agent_registry=None):
    return create_routine_subagent(
        routine_id=ROUTINE_ID,
        description="test routine",
        model=model or _EchoModel(),
        state_manager=state_manager or _FakeStateManager(),
        revision_reader=reader,
        subagent_registry={},
        agent_registry=agent_registry or {},
    )


@pytest.mark.asyncio
async def test_runs_latest_and_pins_revision():
    reader = _FakeRevisionReader({"rev-a": _graph_doc("rev-a output")}, latest="rev-a")
    spec = _spec(reader)

    result = await spec["runnable"].ainvoke(
        {"messages": [HumanMessage("do it")], ROUTINE_REVISIONS_STATE_KEY: {}}
    )

    assert result[ROUTINE_REVISIONS_STATE_KEY] == {ROUTINE_ID: "rev-a"}
    assert result["messages"][-1].content == "rev-a output"


@pytest.mark.asyncio
async def test_reuses_pinned_revision():
    reader = _FakeRevisionReader(
        {"rev-old": _graph_doc("rev-old output"), "rev-new": _graph_doc("rev-new output")},
        latest="rev-new",
    )
    spec = _spec(reader)

    result = await spec["runnable"].ainvoke(
        {
            "messages": [HumanMessage("do it")],
            ROUTINE_REVISIONS_STATE_KEY: {ROUTINE_ID: "rev-old"},
        }
    )

    assert result["messages"][-1].content == "rev-old output"
    assert result[ROUTINE_REVISIONS_STATE_KEY] == {ROUTINE_ID: "rev-old"}
    assert reader.fetch_latest_calls == []


@pytest.mark.asyncio
async def test_missing_pinned_revision_falls_back_to_latest():
    reader = _FakeRevisionReader({"rev-new": _graph_doc("rev-new output")}, latest="rev-new")
    spec = _spec(reader)

    result = await spec["runnable"].ainvoke(
        {
            "messages": [HumanMessage("do it")],
            ROUTINE_REVISIONS_STATE_KEY: {ROUTINE_ID: "rev-missing"},
        }
    )

    assert result["messages"][-1].content == "rev-new output"
    assert result[ROUTINE_REVISIONS_STATE_KEY] == {ROUTINE_ID: "rev-new"}
    assert reader.fetch_calls == [(ROUTINE_ID, "rev-missing")]
    assert reader.fetch_latest_calls == [ROUTINE_ID]


@pytest.mark.asyncio
async def test_second_invocation_hits_cache():
    reader = _FakeRevisionReader({"rev-a": _graph_doc("rev-a output")}, latest="rev-a")
    spec = _spec(reader)

    first = await spec["runnable"].ainvoke(
        {"messages": [HumanMessage("do it")], ROUTINE_REVISIONS_STATE_KEY: {}}
    )
    pin = first[ROUTINE_REVISIONS_STATE_KEY]
    await spec["runnable"].ainvoke(
        {"messages": [HumanMessage("do it again")], ROUTINE_REVISIONS_STATE_KEY: pin}
    )

    assert len(reader.fetch_calls) + len(reader.fetch_latest_calls) == 1


@pytest.mark.asyncio
async def test_invalid_graph_reports_instead_of_raising():
    bad_doc = _graph_doc("irrelevant")
    del bad_doc["entry"]
    reader = _FakeRevisionReader({"rev-bad": bad_doc}, latest="rev-bad")
    spec = _spec(reader)

    result = await spec["runnable"].ainvoke(
        {"messages": [HumanMessage("do it")], ROUTINE_REVISIONS_STATE_KEY: {}}
    )

    assert ROUTINE_REVISIONS_STATE_KEY not in result
    assert ROUTINE_ID in result["messages"][-1].content


@pytest.mark.asyncio
async def test_returns_no_extra_state_keys():
    reader = _FakeRevisionReader({"rev-a": _graph_doc("rev-a output")}, latest="rev-a")
    spec = _spec(reader)

    result = await spec["runnable"].ainvoke(
        {"messages": [HumanMessage("do it")], ROUTINE_REVISIONS_STATE_KEY: {}}
    )

    assert set(result) <= {"messages", ROUTINE_REVISIONS_STATE_KEY}


class _FakeToolCallingModel(FakeMessagesListChatModel):
    """FakeMessagesListChatModel with a no-op bind_tools (deepagents binds tools unconditionally)."""

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        return self


@pytest.mark.asyncio
async def test_through_deep_agent():
    reader = _FakeRevisionReader({"rev-a": _graph_doc("rev-a output")}, latest="rev-a")
    spec = _spec(reader)

    main_model = _FakeToolCallingModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "task",
                        "args": {"description": "run it", "subagent_type": ROUTINE_ID},
                        "id": "call_routine",
                    }
                ],
            ),
            AIMessage(content="All done."),
        ]
    )
    agent = create_deep_agent(
        model=main_model,
        system_prompt="test",
        subagents=[spec],
        state_schema=ProgenitorAgentState,
        tools=[],
    )

    result = await agent.ainvoke({"messages": [HumanMessage("run it")]})

    from langchain_core.messages import ToolMessage

    assert any(isinstance(m, ToolMessage) for m in result["messages"])
    assert result[ROUTINE_REVISIONS_STATE_KEY] == {ROUTINE_ID: "rev-a"}
