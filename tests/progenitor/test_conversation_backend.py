"""The /conversations/ mount: which chats get a file, and when a file is re-rendered."""

from __future__ import annotations

import re
from typing import Annotated, Any, TypedDict

import pytest
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, ToolMessage
from langgraph.channels.delta import DeltaChannel
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.types import _DeltaSnapshot
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import _messages_delta_reducer, add_messages
from langgraph.store.memory import InMemoryStore

from app.progenitor.agent.backends import create_progenitor_backend
from app.progenitor.agent.conversation_backend import ConversationDirectoryBackend
from app.progenitor.conversations.service import ConversationTranscripts
from app.progenitor.conversations.sessions import CHAT_INDEX_KEY, CHAT_SESSIONS_NAMESPACE
from app.progenitor.state.langgraph_store import LangGraphRuntimeStore
from app.progenitor.state.manager import StateManager

MODEL = {"model_provider": "openai"}


class _DeltaMessagesState(TypedDict):
    # The main agent's delta-encoded messages channel, snapshotting every 2
    # updates so a chat's newest checkpoint can hold a ``_DeltaSnapshot``.
    messages: Annotated[
        list[AnyMessage], DeltaChannel(_messages_delta_reducer, snapshot_frequency=2)
    ]


class _Chats:
    def __init__(self) -> None:
        builder = StateGraph(_DeltaMessagesState)
        builder.add_node("noop", lambda _state: {})
        builder.add_edge(START, "noop")
        self.saver = InMemorySaver()
        self.graph = builder.compile(checkpointer=self.saver)
        self.store = InMemoryStore()
        self.state_manager = StateManager(LangGraphRuntimeStore(self.store))
        self.sessions: dict[str, dict[str, Any]] = {}
        self.backend = ConversationDirectoryBackend(
            ConversationTranscripts(self.state_manager, self.saver)
        )

    async def preferences(self, display_name: str) -> None:
        await self.state_manager.save_preferences_config(
            {"display_name": display_name, "timezone": "UTC"}
        )

    async def session(
        self, thread_id: str, *, title: str = "New chat", archived: bool = False
    ) -> None:
        self.sessions[thread_id] = {
            "title": title,
            "thread_id": thread_id,
            "archived": archived,
            "created_at": "2026-10-12T01:00:00Z",
        }
        for key, payload in self.sessions.items():
            await self.store.aput(CHAT_SESSIONS_NAMESPACE, key, {"payload": payload})
        await self.store.aput(
            CHAT_SESSIONS_NAMESPACE, CHAT_INDEX_KEY, {"payload": list(self.sessions)}
        )

    async def turn(self, thread_id: str, question: str, answer: str) -> None:
        messages = [HumanMessage(question), AIMessage(answer, response_metadata=MODEL)]
        await self.graph.ainvoke({"messages": messages}, {"configurable": {"thread_id": thread_id}})

    async def delegated_turn(self, thread_id: str, question: str) -> None:
        """One turn where the model calls ``task`` and a checkpointed subagent answers.

        Mirrors DeepAgents: the subagent graph runs inside the parent's ``tools``
        node, so it checkpoints under its own ``tools:<task_id>`` namespace with
        its ``lc_agent_name`` in the checkpoint metadata.
        """

        class _ChildState(TypedDict):
            messages: Annotated[list[AnyMessage], add_messages]

        child_builder = StateGraph(_ChildState)
        child_builder.add_node(
            "model",
            lambda _state: {
                "messages": [AIMessage("No repos configured.", response_metadata=MODEL)]
            },
        )
        child_builder.add_edge(START, "model")
        child = child_builder.compile().with_config({"metadata": {"lc_agent_name": "repo-manager"}})

        def model(state: dict[str, Any]) -> dict[str, Any]:
            if isinstance(state["messages"][-1], ToolMessage):
                return {"messages": [AIMessage("None are configured.", response_metadata=MODEL)]}
            call = {
                "name": "task",
                "args": {"subagent_type": "repo-manager", "description": "List the repos"},
                "id": "call-task",
                "type": "tool_call",
            }
            return {"messages": [AIMessage("", tool_calls=[call], response_metadata=MODEL)]}

        async def tools(state: dict[str, Any]) -> dict[str, Any]:
            call = state["messages"][-1].tool_calls[0]
            result = await child.ainvoke({"messages": [HumanMessage(call["args"]["description"])]})
            reply = ToolMessage(
                result["messages"][-1].content, tool_call_id=call["id"], name="task"
            )
            return {"messages": [reply]}

        builder = StateGraph(_DeltaMessagesState)
        builder.add_node("model", model)
        builder.add_node("tools", tools)
        builder.add_edge(START, "model")
        builder.add_conditional_edges(
            "model", lambda state: "tools" if state["messages"][-1].tool_calls else END
        )
        builder.add_edge("tools", "model")
        graph = builder.compile(checkpointer=self.saver)
        await graph.ainvoke(
            {"messages": [HumanMessage(question)]}, {"configurable": {"thread_id": thread_id}}
        )

    async def names(self) -> list[str]:
        listing = await self.backend.als("/")
        return [entry["path"] for entry in listing.entries or []]

    async def text(self, path: str) -> str:
        result = await self.backend.aread(path, 0, 10_000)
        assert result.error is None, result.error
        assert result.file_data is not None
        return result.file_data["content"]


@pytest.fixture
async def chats() -> _Chats:
    chats = _Chats()
    await chats.preferences("Tama")
    return chats


async def test_only_non_archived_chats_with_messages_get_a_file(chats: _Chats) -> None:
    await chats.session("aaaaaaaa-live", title="Weekend plans")
    await chats.turn("aaaaaaaa-live", "plan my weekend", "Sure.")
    await chats.session("bbbbbbbb-archived", archived=True)
    await chats.turn("bbbbbbbb-archived", "old stuff", "ok")
    await chats.session("cccccccc-empty")

    names = await chats.names()

    assert len(names) == 1
    assert re.fullmatch(r"/\d{4}-\d{2}-\d{2}_weekend-plans_aaaaaaaa\.txt", names[0])


async def test_archiving_removes_the_file_and_unarchiving_restores_it(chats: _Chats) -> None:
    await chats.session("aaaaaaaa-live")
    await chats.turn("aaaaaaaa-live", "hello there", "Hi.")
    [name] = await chats.names()

    await chats.session("aaaaaaaa-live", archived=True)
    assert await chats.names() == []
    assert (await chats.backend.aread(name)).error is not None

    await chats.session("aaaaaaaa-live", archived=False)
    assert await chats.names() == [name]
    assert "<Tama> hello there" in await chats.text(name)


async def test_chat_whose_newest_checkpoint_is_a_delta_snapshot_renders_in_full(
    chats: _Chats,
) -> None:
    await chats.session("aaaaaaaa-long")
    turns = 0
    tip = None
    while turns < 6 and not isinstance(
        tip and tip.checkpoint["channel_values"].get("messages"), _DeltaSnapshot
    ):
        await chats.turn("aaaaaaaa-long", f"question {turns}", f"answer {turns}")
        tip = await chats.saver.aget_tuple({"configurable": {"thread_id": "aaaaaaaa-long"}})
        turns += 1
    assert tip is not None
    assert isinstance(tip.checkpoint["channel_values"].get("messages"), _DeltaSnapshot)

    [name] = await chats.names()
    text = await chats.text(name)

    asked = re.findall(r"<Tama> question (\d+)", text)
    answered = re.findall(r"<@progenitor> answer (\d+)", text)
    assert asked == answered == [str(i) for i in range(turns)]


async def test_task_subagent_checkpoints_are_inlined_under_the_delegating_call(
    chats: _Chats,
) -> None:
    await chats.session("aaaaaaaa-repos")
    await chats.delegated_turn("aaaaaaaa-repos", "which repos?")
    [name] = await chats.names()

    body = [line.split(" ", 1)[1] for line in (await chats.text(name)).splitlines()[4:]]

    assert body == [
        "<Tama> which repos?",
        "-!- repo-manager [subagent] has joined #aaaaaaaa",
        "-> *repo-manager* List the repos",
        "<@repo-manager> No repos configured.",
        "-!- repo-manager [subagent] has left #aaaaaaaa",
        "<@progenitor> None are configured.",
    ]


async def test_read_includes_new_turns_while_grep_searches_the_last_rendered_copy(
    chats: _Chats,
) -> None:
    await chats.session("aaaaaaaa-live")
    await chats.turn("aaaaaaaa-live", "first question", "first answer")
    [name] = await chats.names()
    assert (await chats.backend.agrep("first answer", "/")).matches

    await chats.turn("aaaaaaaa-live", "second question", "second answer")
    assert (await chats.backend.agrep("second answer", "/")).matches == []

    assert "<@progenitor> second answer" in await chats.text(name)
    assert [m["path"] for m in (await chats.backend.agrep("second answer", "/")).matches] == [name]


async def test_grep_line_numbers_match_read_file(chats: _Chats) -> None:
    await chats.session("aaaaaaaa-live")
    await chats.turn("aaaaaaaa-live", "find the needle", "needle found")
    [name] = await chats.names()

    lines = (await chats.text(name)).split("\n")
    matches = (await chats.backend.agrep("needle", "/")).matches

    assert matches
    for match in matches:
        assert lines[match["line"] - 1] == match["text"]


async def test_grep_path_and_glob_select_files_before_searching(chats: _Chats) -> None:
    await chats.session("aaaaaaaa-weekend", title="Weekend plans")
    await chats.turn("aaaaaaaa-weekend", "plan the weekend", "shared answer")
    await chats.session("bbbbbbbb-repos", title="Repo check")
    await chats.turn("bbbbbbbb-repos", "check the repos", "shared answer")
    names = await chats.names()
    [weekend] = [name for name in names if "weekend" in name]

    def paths(result: Any) -> set[str]:
        return {match["path"] for match in result.matches}

    assert paths(await chats.backend.agrep("shared answer", "/")) == set(names)
    assert paths(await chats.backend.agrep("shared answer", "/", "*_weekend-plans_*")) == {weekend}
    assert paths(await chats.backend.agrep("shared answer", weekend)) == {weekend}


async def test_mount_serves_conversations_through_the_agent_filesystem(
    chats: _Chats, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    # The default StateBackend needs a live graph runtime; it's irrelevant here.
    monkeypatch.setattr("app.progenitor.agent.backends.StateBackend", lambda: None)
    filesystem = create_progenitor_backend(
        project_root=tmp_path, state_manager=chats.state_manager, checkpointer=chats.saver
    )
    await chats.session("aaaaaaaa-live", title="Weekend plans")
    await chats.turn("aaaaaaaa-live", "plan my weekend", "Sure.")

    listing = await filesystem.als("/conversations/")
    [path] = [entry["path"] for entry in listing.entries or []]
    read = await filesystem.aread(path, 0, 100)
    found = await filesystem.agrep("Sure.", "/conversations/", "*_weekend-plans_*")
    written = await filesystem.awrite(path, "x")

    assert re.fullmatch(r"/conversations/\d{4}-\d{2}-\d{2}_weekend-plans_aaaaaaaa\.txt", path)
    assert read.file_data is not None and "<Tama> plan my weekend" in read.file_data["content"]
    assert [match["path"] for match in found.matches] == [path]
    assert written.error is not None


async def test_rename_and_display_name_apply_without_a_new_turn(chats: _Chats) -> None:
    await chats.session("aaaaaaaa-live")
    await chats.turn("aaaaaaaa-live", "hello there", "Hi.")
    [old_name] = await chats.names()

    await chats.session("aaaaaaaa-live", title="Greetings")
    await chats.preferences("Tama W")
    [new_name] = await chats.names()

    assert new_name.endswith("_greetings_aaaaaaaa.txt") and new_name != old_name
    assert (await chats.backend.aread(old_name)).error is not None
    text = await chats.text(new_name)
    assert "-!- Topic for #aaaaaaaa: Greetings" in text
    assert "<TamaW> hello there" in text


async def test_conversation_files_cannot_be_changed(chats: _Chats) -> None:
    await chats.session("aaaaaaaa-live")
    await chats.turn("aaaaaaaa-live", "hello there", "Hi.")
    [name] = await chats.names()

    assert (await chats.backend.awrite(name, "x")).error is not None
    assert (await chats.backend.aedit(name, "Hi.", "Bye.")).error is not None
    assert (await chats.backend.adelete(name)).error is not None
    assert "<@progenitor> Hi." in await chats.text(name)
