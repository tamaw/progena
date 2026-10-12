"""The /conversations/ mount: which chats get a file, and when a file is re-rendered."""

from __future__ import annotations

import re
from typing import Annotated, Any, TypedDict

import pytest
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage
from langgraph.channels.delta import DeltaChannel
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.types import _DeltaSnapshot
from langgraph.graph import START, StateGraph
from langgraph.graph.message import _messages_delta_reducer
from langgraph.store.memory import InMemoryStore

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
