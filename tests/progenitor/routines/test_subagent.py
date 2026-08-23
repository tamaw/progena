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
    resume_routine_thread,
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

    async def get_routine_graph(self, routine_id: str) -> dict[str, Any] | None:
        self.calls += 1
        return self._graph_doc


def _spec(reader, state_manager=None, model=None, agent_registry=None):
    return create_routine_subagent(
        routine_id=ROUTINE_ID,
        description="test routine",
        model=model or _EchoModel(),
        state_manager=state_manager or _FakeStateManager(),
        revision_reader=reader,
        agent_registry=agent_registry or {},
        routine_registry={},
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


def _graph_doc_with_human() -> dict[str, Any]:
    """A single human node that asks a question and echoes the answer back."""
    return {
        "id": ROUTINE_ID,
        "name": "Routine Demo",
        "description": "test routine",
        "version": "2.0.0",
        "entry": "ask",
        "nodes": [
            {
                "id": "ask",
                "kind": "human",
                "output_key": "answer",
                "config": {"message": "What is your favorite color?"},
            }
        ],
        "edges": [],
    }


@pytest.mark.asyncio
async def test_interrupt_surfaces_question_and_thread_id_instead_of_swallowing_it():
    from langgraph.checkpoint.memory import MemorySaver

    reader = _FakeRevisionReader({"rev-a": _graph_doc_with_human()}, latest="rev-a")
    spec = create_routine_subagent(
        routine_id=ROUTINE_ID,
        description="test routine",
        model=_EchoModel(),
        state_manager=_FakeStateManager(),
        revision_reader=reader,
        agent_registry={},
        routine_registry={},
        checkpointer=MemorySaver(),
    )
    result = await spec["runnable"].ainvoke({"messages": [HumanMessage(content="go")]})

    content = result["messages"][-1].content
    assert "waiting for a human answer" in content
    assert "What is your favorite color?" in content
    assert f"routine:{ROUTINE_ID}:rev-a:" in content
    # Not treated as a completed run -- no revision pin recorded for an
    # interrupted (not yet finished) invocation.
    assert ROUTINE_REVISIONS_STATE_KEY not in result


@pytest.mark.asyncio
async def test_resume_routine_thread_completes_after_interrupt():
    import re

    from langgraph.checkpoint.memory import MemorySaver

    checkpointer = MemorySaver()
    reader = _FakeRevisionReader({"rev-a": _graph_doc_with_human()}, latest="rev-a")
    spec = create_routine_subagent(
        routine_id=ROUTINE_ID,
        description="test routine",
        model=_EchoModel(),
        state_manager=_FakeStateManager(),
        revision_reader=reader,
        agent_registry={},
        routine_registry={},
        checkpointer=checkpointer,
    )
    first = await spec["runnable"].ainvoke({"messages": [HumanMessage(content="go")]})
    match = re.search(r"thread_id=('routine:[^']+')", first["messages"][-1].content)
    assert match is not None
    thread_id = match.group(1).strip("'")

    resumed = await resume_routine_thread(
        thread_id=thread_id,
        answer="blue",
        model=_EchoModel(),
        state_manager=_FakeStateManager(),
        revision_reader=reader,
        agent_registry={},
        routine_registry={},
        checkpointer=checkpointer,
    )

    assert resumed["status"] == "completed"
    assert resumed["thread_id"] == thread_id


@pytest.mark.asyncio
async def test_resume_routine_thread_rejects_malformed_thread_id():
    with pytest.raises(ValueError, match="not a routine run thread_id"):
        await resume_routine_thread(
            thread_id="not-a-routine-thread",
            answer="blue",
            model=_EchoModel(),
            state_manager=_FakeStateManager(),
            revision_reader=_FakeRevisionReader({}),
            agent_registry={},
            routine_registry={},
        )
