"""Contract tests for the backend <-> runtime Responses/Conversations API.

Runs a real ``ProgenitorHTTPServer`` (port=0) over a real checkpointed LangGraph
graph and exercises the backend's ``SSEProgenitorClient`` and
``RuntimeConversationReader`` against it, so a drift between what the runtime
sends and what the backend parses fails here.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Annotated, Any, TypedDict

import httpx
import pytest
from deepagents._messages_reducer import _messages_delta_reducer
from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    HumanMessage,
    ToolMessage,
    messages_to_dict,
)
from langgraph.channels.delta import DeltaChannel, _DeltaSnapshot
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import START, StateGraph
from langgraph.graph.message import add_messages

from app.backend.config import ChatServerConfig
from app.backend.runtime_conversations import RuntimeConversationReader
from app.backend.sse_client import SSEProgenitorClient
from app.progenitor.config import RuntimeConfig
from app.progenitor.runtime.http import ProgenitorHTTPServer
from app.progenitor.runtime.queues import RuntimeQueues

pytestmark = pytest.mark.asyncio


class _Wait:
    """Script item: suspend the bridge until ``event`` is set."""

    def __init__(self, event: asyncio.Event) -> None:
        self.event = event


class ScriptedBridge:
    def __init__(self, graph: Any, *, config: Any, state: dict[str, Any], script: Any) -> None:
        self.graph = graph
        self.config = config
        self.state = state
        self._script = script

    async def stream(
        self,
        message: str,
        *,
        thread_id: str,
        received_at: float | None = None,
        source: str | None = None,
        attachments: list[dict[str, Any]] | None = None,
    ):
        items = self._script(message) if callable(self._script) else self._script
        for item in items:
            if isinstance(item, _Wait):
                await item.event.wait()
                continue
            yield item


def make_bridge_factory(script: Any):
    def factory(graph: Any, *, config: Any, state: dict[str, Any]) -> ScriptedBridge:
        return ScriptedBridge(graph, config=config, state=state, script=script)

    return factory


class _MessagesState(TypedDict):
    messages: Annotated[list, add_messages]


def _graph() -> Any:
    builder = StateGraph(_MessagesState)
    builder.add_node("noop", lambda _state: {})
    builder.add_edge(START, "noop")
    return builder.compile(checkpointer=InMemorySaver())


class FakeStateManager:
    async def get_current_state(self) -> dict[str, Any]:
        return {"thread_id": "thread-test", "cycle_count": 0}

    async def save_current_state(self, checkpoint: dict[str, Any]) -> None:
        pass


@dataclass
class FakeLoopRunner:
    graph: Any = field(default_factory=_graph)

    def __post_init__(self) -> None:
        self.config = SimpleNamespace(llm=SimpleNamespace(reasoning_enabled=False))
        self.state: dict[str, Any] = {"thread_id": "progenitor_main", "cycle_count": 0}
        self.heartbeat_context = None
        self.queues = RuntimeQueues(RuntimeConfig())


@dataclass
class _RuntimeServer:
    server: ProgenitorHTTPServer
    loop_runner: FakeLoopRunner
    base_url: str


async def _start_runtime_server(*, bridge_factory: Any = None) -> _RuntimeServer:
    loop_runner = FakeLoopRunner()
    server = ProgenitorHTTPServer(state_manager=FakeStateManager(), loop_runner=loop_runner, port=0)
    # Test seam: inject a scripted bridge instead of the real DeepAgentSSEStream.
    assert server._chat_service is not None
    if bridge_factory is not None:
        server._chat_service._bridge_factory = bridge_factory
    await server.start()
    return _RuntimeServer(server=server, loop_runner=loop_runner, base_url=server.base_url)


def _client_config(base_url: str) -> ChatServerConfig:
    return ChatServerConfig(progenitor_runtime_url=base_url)


async def _seed_thread(graph: Any, thread_id: str, messages: list[Any]) -> str:
    """Persist ``messages`` on ``thread_id``; return the resulting checkpoint id."""
    config = {"configurable": {"thread_id": thread_id}}
    await graph.aupdate_state(config, {"messages": messages}, as_node="noop")
    snapshot = await graph.aget_state(config)
    return str(snapshot.config["configurable"]["checkpoint_id"])


async def test_send_message_folds_reasoning_and_collects_chunks() -> None:
    script = [
        {
            "type": "stream_event",
            "event_type": "reasoning_delta",
            "content": "thinking",
            "metadata": {},
        },
        {
            "type": "stream_event",
            "event_type": "reasoning_summary",
            "content": "thinking summary",
            "metadata": {"reasoning_tokens": 42},
        },
        {
            "type": "stream_event",
            "event_type": "usage_update",
            "content": "",
            "metadata": {"usage": {"input_tokens": 13, "output_tokens": 29, "total_tokens": 42}},
        },
        {"type": "stream_chunk", "content": "Hello ", "is_final": False},
        {"type": "stream_chunk", "content": "world", "is_final": False},
        {"type": "stream_chunk", "content": "", "is_final": True},
    ]
    runtime = await _start_runtime_server(bridge_factory=make_bridge_factory(script))
    client = SSEProgenitorClient(_client_config(runtime.base_url))
    try:
        response = await client.send_message("hi", request_id="req-1")
    finally:
        await client.stop()
        await runtime.server.stop()

    assert response.response == "Hello world"
    assert response.reasoning == "thinking summary"
    assert response.reasoning_tokens == 42
    assert response.usage == {"input_tokens": 13, "output_tokens": 29, "total_tokens": 42}


async def test_stream_events_translate_back_to_internal_chunks_and_events() -> None:
    script = [
        {
            "type": "stream_event",
            "event_type": "tool_start",
            "content": "",
            "metadata": {"tool": "x"},
        },
        {"type": "stream_chunk", "content": "ok", "is_final": True},
    ]
    runtime = await _start_runtime_server(bridge_factory=make_bridge_factory(script))
    client = SSEProgenitorClient(_client_config(runtime.base_url))
    try:
        events = [
            event
            async for event in client.send_message_stream_events(
                "hi", request_id="req-2", metadata={"thread_id": "conv-2"}
            )
        ]
    finally:
        await client.stop()
        await runtime.server.stop()

    assert [(e["type"], e.get("event_type"), e["content"], e.get("is_final")) for e in events] == [
        ("stream_event", "tool_start", "", None),
        ("stream_chunk", None, "ok", False),
        ("stream_chunk", None, "", True),
    ]
    assert all(event["request_id"] == "req-2" for event in events)
    assert events[0]["metadata"] == {"tool": "x"}
    sequences = [events[0]["runtime_stream_seq"], events[1]["runtime_stream_seq"]]
    assert sequences == sorted(sequences)
    assert isinstance(events[1]["runtime_emitted_at_ms"], int)


async def test_stream_events_raise_on_failed_response() -> None:
    def raising_script(_message: str):
        raise RuntimeError("kaboom")

    runtime = await _start_runtime_server(bridge_factory=make_bridge_factory(raising_script))
    client = SSEProgenitorClient(_client_config(runtime.base_url))
    try:
        with pytest.raises(RuntimeError, match="kaboom"):
            async for _ in client.send_message_stream_events("hi", request_id="req-3"):
                pass
    finally:
        await client.stop()
        await runtime.server.stop()


async def test_cancel_request_true_for_in_flight_false_for_unknown() -> None:
    gate = asyncio.Event()
    script = [_Wait(gate), {"type": "stream_chunk", "content": "x", "is_final": True}]
    runtime = await _start_runtime_server(bridge_factory=make_bridge_factory(script))
    client = SSEProgenitorClient(_client_config(runtime.base_url))
    task = None
    try:
        assert (await client.cancel_request("unknown-request")) is False

        task = asyncio.create_task(client.send_message("hi", request_id="req-cancel-me"))
        await asyncio.sleep(0.2)
        assert (await client.cancel_request("req-cancel-me")) is True

        gate.set()
        response = await asyncio.wait_for(task, timeout=5.0)
        assert response.response == ""
    finally:
        if task is not None and not task.done():
            task.cancel()
        await client.stop()
        await runtime.server.stop()


async def test_cancel_request_false_when_unreachable() -> None:
    client = SSEProgenitorClient(ChatServerConfig(progenitor_runtime_url="http://127.0.0.1:1"))
    assert (await client.cancel_request("anything")) is False


async def test_delete_conversation_removes_thread_and_refuses_in_flight() -> None:
    gate = asyncio.Event()
    script = [_Wait(gate), {"type": "stream_chunk", "content": "", "is_final": True}]
    runtime = await _start_runtime_server(bridge_factory=make_bridge_factory(script))
    graph = runtime.loop_runner.graph
    await _seed_thread(graph, "conv-delete", [HumanMessage(content="hello", id="h1")])
    client = SSEProgenitorClient(_client_config(runtime.base_url))
    task = None
    try:
        task = asyncio.create_task(
            client.send_message("hi", request_id="req-busy", metadata={"thread_id": "conv-busy"})
        )
        await asyncio.sleep(0.2)
        assert (await client.delete_conversation("conv-busy")) is False

        assert (await client.delete_conversation("conv-delete")) is True
        snapshot = await graph.aget_state({"configurable": {"thread_id": "conv-delete"}})
        assert snapshot.created_at is None
    finally:
        gate.set()
        if task is not None:
            await asyncio.wait_for(task, timeout=5.0)
        await client.stop()
        await runtime.server.stop()


async def test_reader_round_trips_checkpointed_messages_across_pages() -> None:
    """Every LangChain field the backend derives UI data from survives the runtime round trip."""
    runtime = await _start_runtime_server()
    turns: list[Any] = []
    for index in range(40):
        call_id = f"call_{index}"
        turns.extend(
            [
                HumanMessage(
                    content=f"question {index}",
                    id=f"h{index}",
                    additional_kwargs={
                        "created_at": 1791690000.0 + index,
                        "attachments": [{"id": f"att{index}", "name": "a.txt"}],
                    },
                ),
                AIMessage(
                    id=f"resp_{index}",
                    content=[
                        {
                            "type": "reasoning",
                            "id": f"rs_{index}",
                            "summary": [{"type": "summary_text", "text": "why"}],
                        },
                        {
                            "type": "function_call",
                            "id": f"fc_{index}",
                            "call_id": call_id,
                            "name": "lookup",
                            "arguments": '{"q": 1}',
                        },
                    ],
                    tool_calls=[{"id": call_id, "name": "lookup", "args": {"q": 1}}],
                    usage_metadata={
                        "input_tokens": 10,
                        "output_tokens": 5,
                        "total_tokens": 15,
                        "output_token_details": {"reasoning": 3},
                    },
                    response_metadata={"id": f"resp_{index}"},
                    additional_kwargs={"display_agent_name": "Progenitor"},
                ),
                ToolMessage(
                    content="result",
                    tool_call_id=call_id,
                    id=f"t{index}",
                    artifact={"summary": "s"},
                ),
                AIMessage(
                    id=f"resp_final_{index}",
                    content=[
                        {
                            "type": "text",
                            "id": f"msg_{index}",
                            "text": f"answer {index}",
                            "annotations": [],
                        }
                    ],
                ),
            ]
        )
    checkpoint_id = await _seed_thread(runtime.loop_runner.graph, "conv-long", turns)
    reader = RuntimeConversationReader(runtime.base_url)
    try:
        messages = await asyncio.to_thread(reader.get_messages, "conv-long", checkpoint_id)
        missing = await asyncio.to_thread(reader.get_messages, "conv-missing", None)
    finally:
        await runtime.server.stop()

    assert messages_to_dict(messages) == messages_to_dict(turns)
    assert missing == []


async def test_reader_returns_full_history_when_latest_checkpoint_is_a_delta_snapshot() -> None:
    """Regression: chats whose tip checkpoint stores a ``_DeltaSnapshot`` showed as empty.

    Uses the same ``DeltaChannel`` messages field as deepagents' agent state,
    with a snapshot every 2 updates so the tip checkpoint holds a snapshot.
    """

    class _DeltaMessagesState(TypedDict):
        messages: Annotated[
            list[AnyMessage], DeltaChannel(_messages_delta_reducer, snapshot_frequency=2)
        ]

    builder = StateGraph(_DeltaMessagesState)
    builder.add_node("noop", lambda _state: {})
    builder.add_edge(START, "noop")
    graph = builder.compile(checkpointer=InMemorySaver())
    runtime = await _start_runtime_server()
    runtime.loop_runner.graph = graph
    config = {"configurable": {"thread_id": "conv-snapshot"}}
    turns: list[Any] = []
    tip = None
    for index in range(6):
        turn = [
            HumanMessage(content=f"question {index}", id=f"h{index}"),
            AIMessage(content=f"answer {index}", id=f"a{index}"),
        ]
        turns.extend(turn)
        await graph.ainvoke({"messages": turn}, config)
        tip = await graph.checkpointer.aget_tuple(config)
        if isinstance(tip.checkpoint["channel_values"].get("messages"), _DeltaSnapshot):
            break
    assert tip is not None
    assert isinstance(tip.checkpoint["channel_values"].get("messages"), _DeltaSnapshot)
    checkpoint_id = tip.checkpoint["id"]

    reader = RuntimeConversationReader(runtime.base_url)
    try:
        messages = await asyncio.to_thread(reader.get_messages, "conv-snapshot", checkpoint_id)
    finally:
        await runtime.server.stop()

    assert messages_to_dict(messages) == messages_to_dict(turns)


async def test_event_forwarding_from_runtime_broadcast() -> None:
    runtime = await _start_runtime_server()
    received: list[tuple[str, dict[str, Any]]] = []

    def callback(event_type: str, data: dict[str, Any]) -> None:
        received.append((event_type, data))

    client = SSEProgenitorClient(_client_config(runtime.base_url), event_callback=callback)
    try:
        await client.start()
        await asyncio.sleep(0.2)
        await runtime.loop_runner.queues.push_event("tick", "hello", {"n": 1})
        for _ in range(50):
            if received:
                break
            await asyncio.sleep(0.1)
    finally:
        await client.stop()
        await runtime.server.stop()

    assert received
    event_type, payload = received[0]
    assert event_type == "tick"
    assert payload["content"] == "hello"
    assert payload["metadata"] == {"n": 1}


async def test_start_liveness_and_status() -> None:
    runtime = await _start_runtime_server()
    client = SSEProgenitorClient(_client_config(runtime.base_url))
    try:
        assert client.is_running is False
        await client.start()
        assert client.is_running is True
        status = client.get_status()
        assert status["base_url"] == runtime.base_url
        assert status["running"] is True
    finally:
        await client.stop()
        await runtime.server.stop()


async def test_start_raises_when_runtime_unreachable() -> None:
    client = SSEProgenitorClient(ChatServerConfig(progenitor_runtime_url="http://127.0.0.1:1"))
    with pytest.raises(RuntimeError):
        await asyncio.wait_for(client.start(), timeout=15.0)


async def test_stream_times_out_on_stuck_runtime() -> None:
    gate = asyncio.Event()  # never set
    runtime = await _start_runtime_server(bridge_factory=make_bridge_factory([_Wait(gate)]))
    config = _client_config(runtime.base_url)
    config.progenitor_timeout = 0.3
    client = SSEProgenitorClient(config)
    try:
        with pytest.raises(httpx.ReadTimeout):
            async for _ in client.send_message_stream_events("hi", request_id="req-stuck"):
                pass
    finally:
        gate.set()
        await client.stop()
        await runtime.server.stop()
