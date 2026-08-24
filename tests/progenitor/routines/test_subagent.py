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
        self.fetch_revision_id_for_content_calls: list[str] = []

    async def fetch_latest(self, routine_id: str) -> tuple[str, dict[str, Any]] | None:
        self.fetch_latest_calls.append(routine_id)
        if self._latest is None:
            return None
        return self._latest, self._revisions[self._latest]

    async def fetch(self, routine_id: str, revision_id: str) -> dict[str, Any] | None:
        self.fetch_calls.append((routine_id, revision_id))
        return self._revisions.get(revision_id)

    async def fetch_revision_id_for_content(
        self, routine_id: str, graph: dict[str, Any]
    ) -> str | None:
        self.fetch_revision_id_for_content_calls.append(routine_id)
        for revision_id, content in self._revisions.items():
            if content == graph:
                return revision_id
        return None


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
async def test_unpinned_run_prefers_live_document_over_fetch_latest():
    """Reproduces the real bug this fixes: `routine_graph_revisions` is a
    content-hash-deduplicated audit log (`ON CONFLICT ... DO NOTHING`), so
    if live content ever reverts to a byte-identical prior state, the row
    with the newest `captured_at` -- what `fetch_latest` returns -- is no
    longer necessarily what's live. An unpinned run must compile and
    execute the live document, never `fetch_latest`'s answer, and must
    never even call `fetch_latest` when a live document exists."""
    reader = _FakeRevisionReader(
        {"rev-stale-but-newest-captured": _graph_doc("STALE content, must not run")},
        latest="rev-stale-but-newest-captured",
    )
    live_doc = _graph_doc("live content, must run")
    spec = _spec(reader, state_manager=_FakeStateManager(live_doc))

    result = await spec["runnable"].ainvoke(
        {"messages": [HumanMessage("do it")], ROUTINE_REVISIONS_STATE_KEY: {}}
    )

    assert result["messages"][-1].content == "live content, must run"
    assert reader.fetch_latest_calls == []


@pytest.mark.asyncio
async def test_unpinned_run_tags_itself_with_the_matching_revision_id():
    """The live document is always what's compiled/executed; the matching
    revision_id (if the exact content has been captured) only *tags* the
    run -- for the Runs panel and the canvas's run-staleness diffing."""
    live_doc = _graph_doc("captured content")
    reader = _FakeRevisionReader({"rev-captured": live_doc})
    spec = _spec(reader, state_manager=_FakeStateManager(live_doc))

    result = await spec["runnable"].ainvoke(
        {"messages": [HumanMessage("do it")], ROUTINE_REVISIONS_STATE_KEY: {}}
    )

    assert result[ROUTINE_REVISIONS_STATE_KEY] == {ROUTINE_ID: "rev-captured"}
    assert reader.fetch_revision_id_for_content_calls == [ROUTINE_ID]


@pytest.mark.asyncio
async def test_unpinned_run_with_uncaptured_live_content_runs_untagged():
    """A just-edited, not-yet-captured live document still runs correctly
    -- it simply has no revision_id to tag itself with."""
    live_doc = _graph_doc("brand new, uncaptured content")
    reader = _FakeRevisionReader({})
    spec = _spec(reader, state_manager=_FakeStateManager(live_doc))

    result = await spec["runnable"].ainvoke(
        {"messages": [HumanMessage("do it")], ROUTINE_REVISIONS_STATE_KEY: {}}
    )

    assert result["messages"][-1].content == "brand new, uncaptured content"
    assert ROUTINE_REVISIONS_STATE_KEY not in result


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


def _graph_doc_chain() -> dict[str, Any]:
    """Three sequential prompt nodes, a -> b -> c, each echoing a distinct marker."""
    return {
        "id": ROUTINE_ID,
        "name": "Routine Demo",
        "description": "test routine",
        "version": "2.0.0",
        "entry": "a",
        "nodes": [
            {
                "id": "a",
                "kind": "prompt",
                "output_key": "a",
                "config": {"prompt_template": "step-a"},
            },
            {
                "id": "b",
                "kind": "prompt",
                "output_key": "b",
                "config": {"prompt_template": "step-b"},
            },
            {
                "id": "c",
                "kind": "prompt",
                "output_key": "c",
                "config": {"prompt_template": "step-c"},
            },
        ],
        "edges": [
            {"from": "a", "to": "b", "level": "normal"},
            {"from": "b", "to": "c", "level": "normal"},
        ],
    }


@pytest.mark.asyncio
async def test_interactive_target_node_runs_through_it():
    from langgraph.checkpoint.memory import MemorySaver

    model = _EchoModel()
    reader = _FakeRevisionReader({"rev-a": _graph_doc_chain()}, latest="rev-a")
    spec = create_routine_subagent(
        routine_id=ROUTINE_ID,
        description="test routine",
        model=model,
        state_manager=_FakeStateManager(),
        revision_reader=reader,
        agent_registry={},
        routine_registry={},
        checkpointer=MemorySaver(),
    )
    await spec["runnable"].ainvoke(
        {
            "messages": [HumanMessage(content="go")],
            "_interactive_session_id": "sess-1",
            "_interactive_target_node": "b",
        }
    )
    # "a" and "b" ran -- "c" never invoked the model.
    assert len(model.calls) == 2
    assert model.calls[0][-1].content == "step-a"
    assert model.calls[1][-1].content == "step-b"


@pytest.mark.asyncio
async def test_interactive_session_id_resumes_instead_of_restarting():
    from langgraph.checkpoint.memory import MemorySaver

    model = _EchoModel()
    reader = _FakeRevisionReader({"rev-a": _graph_doc_chain()}, latest="rev-a")
    spec = create_routine_subagent(
        routine_id=ROUTINE_ID,
        description="test routine",
        model=model,
        state_manager=_FakeStateManager(),
        revision_reader=reader,
        agent_registry={},
        routine_registry={},
        checkpointer=MemorySaver(),
    )
    await spec["runnable"].ainvoke(
        {
            "messages": [HumanMessage(content="go")],
            "_interactive_session_id": "sess-1",
            "_interactive_target_node": "b",
        }
    )
    assert len(model.calls) == 2  # "a" and "b" ran so far

    result = await spec["runnable"].ainvoke(
        {
            "messages": [HumanMessage(content="go")],
            "_interactive_session_id": "sess-1",
        }
    )
    # "c" ran -- "a"/"b" were NOT re-invoked, proving this resumed the
    # same checkpointed thread rather than starting a fresh run from input.
    assert len(model.calls) == 3
    assert model.calls[2][-1].content == "step-c"
    assert result[ROUTINE_REVISIONS_STATE_KEY] == {ROUTINE_ID: "rev-a"}


@pytest.mark.asyncio
async def test_interactive_session_id_is_reflected_in_thread_id():
    from langgraph.checkpoint.memory import MemorySaver

    reader = _FakeRevisionReader({"rev-a": _graph_doc_chain()}, latest="rev-a")
    checkpointer = MemorySaver()
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
    await spec["runnable"].ainvoke(
        {"messages": [HumanMessage(content="go")], "_interactive_session_id": "sess-xyz"}
    )
    checkpoints = [
        c
        async for c in checkpointer.alist(
            {"configurable": {"thread_id": f"routine:{ROUTINE_ID}:rev-a:sess-xyz"}}
        )
    ]
    assert len(checkpoints) > 0
