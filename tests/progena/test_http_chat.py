"""Contract tests for the Responses/Conversations API HTTP endpoints."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Annotated, Any, TypedDict

import httpx
import pytest
from aiohttp import web
from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.runtime import Runtime
from langgraph.store.memory import InMemoryStore

import src.progena.llm.provider_context as provider_runtime
from src.progena.config import LLMConfig, ProgenaConfig, RuntimeConfig
from src.progena.orchestration.nodes.chat import chat_node
from src.progena.persistence.store import LangGraphRuntimeStore
from src.progena.runtime.chat_queue import ChatQueueStore
from src.progena.runtime.http.chat import RuntimeChatService
from src.progena.runtime.queues import RuntimeQueues
from src.progena.runtime.streaming.deepagent_stream import DeepAgentSSEStream
from src.progena.state.manager import StateManager

pytestmark = pytest.mark.asyncio


# -- Test doubles -------------------------------------------------------


class _Wait:
    """Script item: suspend the bridge until ``event`` is set."""

    def __init__(self, event: asyncio.Event) -> None:
        self.event = event


class _Raise:
    """Script item: raise ``exc`` from the bridge's stream."""

    def __init__(self, exc: Exception) -> None:
        self.exc = exc


class ScriptedBridge:
    """Fake DeepAgentSSEStream bridge that replays a scripted item list."""

    def __init__(
        self,
        graph: Any,
        *,
        config: Any,
        state: dict[str, Any],
        script: Any,
    ) -> None:
        self.graph = graph
        self.config = config
        self.state = state
        self._script = script
        self.thread_id: str | None = None
        self.resume = False
        self.message_id: str | None = None
        self.attachments: list[dict[str, Any]] | None = None

    async def stream(
        self,
        message: str,
        *,
        thread_id: str,
        received_at: float | None = None,
        attachments: list[dict[str, Any]] | None = None,
        source: str | None = None,
        message_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        resume: bool = False,
    ):
        self.thread_id = thread_id
        self.resume = resume
        self.message_id = message_id
        self.attachments = attachments
        items = self._script(message) if callable(self._script) else self._script
        for item in items:
            if isinstance(item, _Wait):
                await item.event.wait()
                continue
            if isinstance(item, _Raise):
                raise item.exc
            yield item


def make_bridge_factory(script: Any) -> Callable[..., ScriptedBridge]:
    def factory(graph: Any, *, config: Any, state: dict[str, Any]) -> ScriptedBridge:
        return ScriptedBridge(
            graph,
            config=config,
            state=state,
            script=script,
        )

    return factory


class FakeStateManager:
    def __init__(self) -> None:
        self.saved_checkpoints: list[dict[str, Any]] = []
        self._current_state: dict[str, Any] = {}
        self._runtime_store = LangGraphRuntimeStore(InMemoryStore())

    async def get_current_state(self, thread_id: str | None = None) -> dict[str, Any]:
        return dict(self._current_state)

    async def save_current_state(
        self, checkpoint: dict[str, Any], thread_id: str | None = None
    ) -> None:
        self._current_state.update(checkpoint)
        self.saved_checkpoints.append({"thread_id": thread_id, **checkpoint})

    async def update_current_state(
        self, updates: dict[str, Any], thread_id: str | None = None
    ) -> None:
        self._current_state.update(updates)

    async def merge_session(self, thread_id: str, fields: dict[str, Any]) -> bool:
        return False

    async def get_preferences_config(self) -> dict[str, Any]:
        return {"display_name": "user", "timezone": "UTC"}


@dataclass
class FakeHeartbeatContext:
    state_manager: FakeStateManager


def _make_real_graph() -> Any:
    """Build a tiny compiled LangGraph with InMemorySaver for aget_state/adelete_thread tests."""

    class _State(TypedDict):
        messages: Annotated[list, add_messages]

    builder = StateGraph(_State)
    builder.add_node("n", lambda s: {"messages": [AIMessage(content="hi", id="a1")]})
    builder.add_edge(START, "n")
    builder.add_edge("n", END)
    return builder.compile(checkpointer=InMemorySaver())


@dataclass
class FakeLoopRunner:
    graph: Any = field(default_factory=lambda: _make_real_graph())
    thread_id: str = "progena_main"

    def __post_init__(self) -> None:
        self.config = ProgenaConfig(
            llm=LLMConfig(provider_id="test-provider", reasoning_enabled=False)
        )
        self.state: dict[str, Any] = {"thread_id": self.thread_id, "cycle_count": 0}
        self.state_manager = FakeStateManager()
        self.heartbeat_context = FakeHeartbeatContext(self.state_manager)
        self.queues = RuntimeQueues(RuntimeConfig())


class _ProviderRepository:
    """Isolated persisted-provider boundary for the real bridge test."""

    def resolve(
        self, provider_id: str | None = None, model_override: str | None = None
    ) -> dict[str, Any]:
        return {
            "provider_id": provider_id or "test-provider",
            "provider_kind": "foundry",
            "base_url": "https://example.openai.azure.com/openai/v1",
            "api_key": "test-key",
            "model_default": model_override or "gpt-4.1",
            "revision": 1,
        }


@dataclass
class _ChatServer:
    runner: web.AppRunner
    service: RuntimeChatService
    base_url: str


async def _start_chat_server(
    loop_runner: Any, *, bridge_factory: Any = None, start_drain: bool = False
) -> _ChatServer:
    service = RuntimeChatService(loop_runner, bridge_factory=bridge_factory)
    if start_drain:
        await service.start()
    app = web.Application()
    app.add_routes(service.routes())
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0, reuse_address=True)
    await site.start()
    server = getattr(site, "_server", None)
    sockets = getattr(server, "sockets", None)
    port = int(sockets[0].getsockname()[1]) if sockets else 0
    return _ChatServer(runner=runner, service=service, base_url=f"http://127.0.0.1:{port}")


async def _stop_chat_server(server: _ChatServer) -> None:
    await server.service.stop()
    await server.runner.cleanup()


TEST_CONVERSATION = "test-conversation"


async def _post_responses(
    client: httpx.AsyncClient,
    base_url: str,
    input_text: str,
    request_id: str,
    metadata: dict[str, Any] | None = None,
    stream: bool = True,
    conversation: str | None = TEST_CONVERSATION,
) -> list[tuple[str, dict[str, Any]]]:
    frames: list[tuple[str, dict[str, Any]]] = []
    body: dict[str, Any] = {
        "input": input_text,
        "stream": stream,
        "metadata": {"request_id": request_id, **(metadata or {})},
    }
    if conversation is not None:
        body["conversation"] = conversation
    async with client.stream("POST", f"{base_url}/v1/responses", json=body) as response:
        assert response.status_code == 200
        event_name: str | None = None
        async for line in response.aiter_lines():
            if line == "" or line.startswith(":"):
                continue
            if line.startswith("event: "):
                event_name = line[len("event: ") :]
                continue
            if line.startswith("data: "):
                assert event_name is not None
                data = json.loads(line[len("data: ") :])
                frames.append((event_name, data))
                if event_name in ("response.completed", "response.failed", "response.incomplete"):
                    break
    return frames


async def _follow_response_stream(
    client: httpx.AsyncClient, base_url: str, response_id: str, *, cursor: int | None = None
) -> list[tuple[str, dict[str, Any]]]:
    url = f"{base_url}/v1/responses/{response_id}/stream"
    if cursor is not None:
        url = f"{url}?cursor={cursor}"
    frames: list[tuple[str, dict[str, Any]]] = []
    async with client.stream("GET", url) as response:
        assert response.status_code == 200
        event_name: str | None = None
        async for line in response.aiter_lines():
            if line == "" or line.startswith(":") or line.startswith("id: "):
                continue
            if line.startswith("event: "):
                event_name = line[len("event: ") :]
                continue
            if line.startswith("data: "):
                assert event_name is not None
                data = json.loads(line[len("data: ") :])
                frames.append((event_name, data))
                if event_name in ("response.completed", "response.failed", "response.incomplete"):
                    break
    return frames


async def _next_runtime_event(queues: RuntimeQueues, event_type: str) -> dict[str, Any]:
    while True:
        event = await queues.get_event(timeout=1.0)
        assert event is not None
        if event["event_type"] == event_type:
            return event


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=None))


# -- Tests ----------------------------------------------------------------


async def test_responses_stream_envelope_order_and_completed() -> None:
    loop_runner = FakeLoopRunner()
    script = [
        {"type": "stream_chunk", "content": "Hello", "is_final": False},
        {
            "type": "stream_event",
            "event_type": "node_update",
            "content": "",
            "metadata": {"node_name": "respond"},
        },
        {"type": "stream_chunk", "content": "", "is_final": True},
    ]
    server = await _start_chat_server(loop_runner, bridge_factory=make_bridge_factory(script))
    try:
        async with _client() as client:
            frames = await _post_responses(client, server.base_url, "hi", "resp-1")
    finally:
        await _stop_chat_server(server)

    event_names = [n for n, _ in frames]
    assert event_names[0] == "response.created"
    assert event_names[1] == "response.in_progress"
    assert event_names[2] == "response.output_item.added"
    assert event_names[3] == "response.content_part.added"
    assert event_names[4] == "response.output_text.delta"
    assert event_names[5] == "progena.stream_event"
    assert event_names[6] == "response.output_text.done"
    assert event_names[7] == "response.content_part.done"
    assert event_names[8] == "response.output_item.done"
    assert event_names[9] == "response.completed"

    # Delta carries content
    deltas = [p for n, p in frames if n == "response.output_text.delta"]
    assert deltas[0]["delta"] == "Hello"
    assert deltas[0]["item_id"] == "msg_resp-1"

    # Completed response object
    completed = frames[-1][1]
    assert completed["response"]["status"] == "completed"
    # Fake bridge does not add messages to the graph, so output is empty.
    assert completed["response"]["output"] == []


async def test_responses_rejects_missing_input_and_bad_json() -> None:
    loop_runner = FakeLoopRunner()
    server = await _start_chat_server(loop_runner)
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(f"{server.base_url}/v1/responses", json={})
            assert resp.status_code == 400
            assert resp.json()["error"]["code"] == "bad_request"

            resp2 = await client.post(
                f"{server.base_url}/v1/responses",
                content=b"not json",
                headers={"Content-Type": "application/json"},
            )
            assert resp2.status_code == 400
            assert resp2.json()["error"]["code"] == "bad_request"
    finally:
        await _stop_chat_server(server)


async def test_responses_503_when_graph_missing_astream() -> None:
    loop_runner = FakeLoopRunner(graph=object())
    server = await _start_chat_server(loop_runner)
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(f"{server.base_url}/v1/responses", json={"input": "hi"})
            assert resp.status_code == 503
            assert resp.json()["error"]["code"] == "graph_unavailable"
    finally:
        await _stop_chat_server(server)


async def test_responses_serializes_requests_and_emits_queued() -> None:
    loop_runner = FakeLoopRunner()
    gate = asyncio.Event()
    script = {
        "first": [_Wait(gate), {"type": "stream_chunk", "content": "first-done", "is_final": True}],
        "second": [{"type": "stream_chunk", "content": "second-done", "is_final": True}],
    }
    server = await _start_chat_server(
        loop_runner, bridge_factory=make_bridge_factory(lambda msg: script[msg])
    )
    task_a = task_b = None
    queued_content: list[tuple[str, dict[str, Any]]] = []
    replayed: list[tuple[str, dict[str, Any]]] = []
    try:
        async with _client() as client:
            task_a = asyncio.create_task(
                _post_responses(client, server.base_url, "first", "resp-a")
            )
            await asyncio.sleep(0.2)
            task_b = asyncio.create_task(
                _post_responses(client, server.base_url, "second", "resp-b")
            )
            await asyncio.sleep(0.2)

            queued_event = await loop_runner.queues.get_event(timeout=1.0)
            assert queued_event is not None
            assert queued_event["event_type"] == "chat_queue_updated"
            assert queued_event["metadata"]["messages"][0]["id"] == "user_resp-b"

            gate.set()
            frames_a = await asyncio.wait_for(task_a, timeout=5.0)
            frames_b = await asyncio.wait_for(task_b, timeout=5.0)
            queued_content = await _follow_response_stream(client, server.base_url, "resp-b")
            replayed = await _follow_response_stream(client, server.base_url, "resp-b", cursor=0)
    finally:
        gate.set()
        for task in (task_a, task_b):
            if task is not None and not task.done():
                task.cancel()
        await _stop_chat_server(server)

    assert frames_a[-1][0] == "response.completed"
    assert frames_b[0][0] == "response.created"
    assert frames_b[1][0] == "response.queued"
    assert frames_b[2][0] == "response.completed"
    assert frames_b[-1][0] == "response.completed"
    assert any(
        event == "response.output_text.delta" and payload["delta"] == "second-done"
        for event, payload in queued_content
    )
    assert queued_content[-1][0] == "response.completed"
    assert replayed[0][0] == "response.in_progress"


async def test_deferred_response_stop_cancels_its_active_execution() -> None:
    loop_runner = FakeLoopRunner()
    release_first = asyncio.Event()
    release_second = asyncio.Event()
    script = {
        "first": [
            _Wait(release_first),
            {"type": "stream_chunk", "content": "first", "is_final": True},
        ],
        "second": [
            _Wait(release_second),
            {"type": "stream_chunk", "content": "second", "is_final": True},
        ],
    }
    server = await _start_chat_server(
        loop_runner, bridge_factory=make_bridge_factory(lambda message: script[message])
    )
    first_task = second_task = None
    try:
        async with _client() as client:
            first_task = asyncio.create_task(
                _post_responses(client, server.base_url, "first", "response-a")
            )
            await asyncio.sleep(0.05)
            second_task = asyncio.create_task(
                _post_responses(client, server.base_url, "second", "response-b")
            )
            await _next_runtime_event(loop_runner.queues, "chat_queue_updated")
            release_first.set()
            await asyncio.wait_for(first_task, timeout=5.0)
            await asyncio.wait_for(second_task, timeout=5.0)
            started = await _next_runtime_event(loop_runner.queues, "chat_queue_execution_started")
            assert started["metadata"]["request_id"] == "response-b"

            stopped = await client.post(f"{server.base_url}/v1/responses/response-b/cancel")
            assert stopped.status_code == 200
            release_second.set()
            frames = await _follow_response_stream(client, server.base_url, "response-b")
            assert frames[-1][0] == "response.incomplete"
            terminal = await _next_runtime_event(
                loop_runner.queues, "chat_queue_execution_cancelled"
            )
            assert terminal["metadata"]["request_id"] == "response-b"
    finally:
        release_first.set()
        release_second.set()
        for task in (first_task, second_task):
            if task is not None and not task.done():
                task.cancel()
        await _stop_chat_server(server)


async def test_send_now_cancels_captured_root_and_repeated_command_keeps_new_turn() -> None:
    loop_runner = FakeLoopRunner()
    release_first = asyncio.Event()
    release_selected = asyncio.Event()
    script = {
        "first": [
            _Wait(release_first),
            {"type": "stream_chunk", "content": "first", "is_final": True},
        ],
        "normal": [{"type": "stream_chunk", "content": "normal", "is_final": True}],
        "selected": [
            _Wait(release_selected),
            {"type": "stream_chunk", "content": "selected", "is_final": True},
        ],
    }
    server = await _start_chat_server(
        loop_runner, bridge_factory=make_bridge_factory(lambda message: script[message])
    )
    first_task = normal_task = selected_task = None
    try:
        async with _client() as client:
            first_task = asyncio.create_task(
                _post_responses(client, server.base_url, "first", "response-a")
            )
            await asyncio.sleep(0.05)
            normal_task = asyncio.create_task(
                _post_responses(client, server.base_url, "normal", "response-b")
            )
            selected_task = asyncio.create_task(
                _post_responses(client, server.base_url, "selected", "response-c")
            )
            await _next_runtime_event(loop_runner.queues, "chat_queue_updated")
            await _next_runtime_event(loop_runner.queues, "chat_queue_updated")
            queue = await client.get(
                f"{server.base_url}/v1/conversations/{TEST_CONVERSATION}/queue"
            )
            snapshot = queue.json()
            selected_id = next(
                item["id"] for item in snapshot["messages"] if item["request_id"] == "response-c"
            )
            command = {
                "action": "send_now",
                "message_id": selected_id,
                "revision": snapshot["revision"],
                "command_id": "select-c",
            }
            selected = await client.post(
                f"{server.base_url}/v1/conversations/{TEST_CONVERSATION}/queue/actions",
                json=command,
            )
            assert selected.status_code == 200
            assert (await asyncio.wait_for(first_task, timeout=5.0))[-1][0] == "response.incomplete"
            await asyncio.wait_for(normal_task, timeout=5.0)
            await asyncio.wait_for(selected_task, timeout=5.0)
            started = await _next_runtime_event(loop_runner.queues, "chat_queue_execution_started")
            assert started["metadata"]["request_id"] == "response-c"

            repeated = await client.post(
                f"{server.base_url}/v1/conversations/{TEST_CONVERSATION}/queue/actions",
                json=command,
            )
            assert repeated.status_code == 200
            release_selected.set()
            selected_frames = await _follow_response_stream(client, server.base_url, "response-c")
            assert selected_frames[-1][0] == "response.completed"
            next_started = await _next_runtime_event(
                loop_runner.queues, "chat_queue_execution_started"
            )
            assert next_started["metadata"]["request_id"] == "response-b"
    finally:
        release_first.set()
        release_selected.set()
        for task in (first_task, normal_task, selected_task):
            if task is not None and not task.done():
                task.cancel()
        await _stop_chat_server(server)


async def test_startup_recovers_claims_before_human_and_resumes_native_pending_graph() -> None:
    class RecoveryGraph:
        def __init__(self, messages: list[Any], next_nodes: tuple[str, ...]) -> None:
            self.messages = messages
            self.next_nodes = next_nodes

        async def aget_state(self, config: dict[str, Any]) -> Any:
            del config
            return SimpleNamespace(values={"messages": self.messages}, next=self.next_nodes)

    async def run_case(
        messages: list[Any], next_nodes: tuple[str, ...], expected_resume: bool | None
    ) -> None:
        loop_runner = FakeLoopRunner()
        queue = ChatQueueStore(loop_runner.heartbeat_context.state_manager._runtime_store)
        _snapshot, message_id = await queue.enqueue(
            "restart-thread",
            text="durable input",
            attachments=[{"url": "https://example.test/file"}],
            metadata={},
            source="test",
            received_at=1.0,
            response_id="restart-response",
            model="test",
        )
        claimed = await queue.claim_next("restart-thread")
        assert claimed is not None and claimed.id == message_id
        loop_runner.graph = RecoveryGraph(messages, next_nodes)
        bridges: list[ScriptedBridge] = []

        def factory(graph: Any, *, config: Any, state: dict[str, Any]) -> ScriptedBridge:
            bridge = ScriptedBridge(
                graph,
                config=config,
                state=state,
                script=[{"type": "stream_chunk", "content": "done", "is_final": True}],
            )
            bridges.append(bridge)
            return bridge

        service = RuntimeChatService(loop_runner, bridge_factory=factory)
        events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        service._subscribers.add(events)

        async def next_event(event_type: str) -> dict[str, Any]:
            while True:
                event = await asyncio.wait_for(events.get(), timeout=1.0)
                if event["event_type"] == event_type:
                    return event

        await service.start()
        try:
            if expected_resume is None:
                await asyncio.sleep(0)
                assert bridges == []
            else:
                started = await next_event("chat_queue_execution_started")
                assert started["metadata"]["message_id"] == message_id
                await next_event("chat_queue_execution_completed")
                assert len(bridges) == 1
                assert bridges[0].resume is expected_resume
                assert bridges[0].attachments == [{"url": "https://example.test/file"}]
                assert bridges[0].message_id == message_id
            assert (await queue.snapshot("restart-thread"))["messages"] == []
        finally:
            service._subscribers.discard(events)
            await service.stop()

    await run_case([], (), False)
    await run_case(
        [HumanMessage(id="user_restart-response", content="durable input")],
        ("model",),
        True,
    )
    await run_case(
        [
            HumanMessage(id="user_restart-response", content="durable input"),
            AIMessage(id="assistant_restart", content="done"),
        ],
        (),
        None,
    )


async def test_restart_settles_send_now_cancelled_claim_before_running_selected_turn() -> None:
    class RestartGraph:
        def __init__(self) -> None:
            self.messages: list[Any] = [HumanMessage(id="user_running", content="running")]
            self.next_nodes = ("model",)
            self.cancelled_updates = 0

        async def aget_state(self, config: dict[str, Any]) -> Any:
            del config
            return SimpleNamespace(values={"messages": self.messages}, next=self.next_nodes)

        async def aupdate_state(
            self, config: dict[str, Any], updates: dict[str, Any], *, as_node: str
        ) -> None:
            del config, as_node
            self.messages.extend(updates["messages"])
            self.next_nodes = ()
            self.cancelled_updates += 1

    loop_runner = FakeLoopRunner()
    queue = ChatQueueStore(loop_runner.heartbeat_context.state_manager._runtime_store)
    _snapshot, running_id = await queue.enqueue(
        "restart-send-now",
        text="running",
        attachments=[],
        metadata={},
        source="test",
        received_at=1.0,
        response_id="running",
        model="test",
    )
    _snapshot, selected_id = await queue.enqueue(
        "restart-send-now",
        text="selected",
        attachments=[],
        metadata={},
        source="test",
        received_at=2.0,
        response_id="selected",
        model="test",
    )
    claimed = await queue.claim_next("restart-send-now")
    assert claimed is not None and claimed.id == running_id
    snapshot = await queue.snapshot("restart-send-now")
    _updated, applied = await queue.action(
        "restart-send-now",
        {
            "action": "send_now",
            "message_id": selected_id,
            "revision": snapshot["revision"],
            "command_id": "send-selected-now",
        },
        include_applied=True,
    )
    assert applied is True

    graph = RestartGraph()
    loop_runner.graph = graph
    bridges: list[ScriptedBridge] = []

    def factory(graph: Any, *, config: Any, state: dict[str, Any]) -> ScriptedBridge:
        bridge = ScriptedBridge(
            graph,
            config=config,
            state=state,
            script=[{"type": "stream_chunk", "content": "selected", "is_final": True}],
        )
        bridges.append(bridge)
        return bridge

    service = RuntimeChatService(loop_runner, bridge_factory=factory)
    events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    service._subscribers.add(events)

    async def next_event(event_type: str) -> dict[str, Any]:
        while True:
            event = await asyncio.wait_for(events.get(), timeout=1.0)
            if event["event_type"] == event_type:
                return event

    await service.start()
    try:
        cancelled = await next_event("chat_queue_execution_cancelled")
        assert cancelled["metadata"]["request_id"] == "running"
        assert cancelled["metadata"]["response"]["status"] == "cancelled"
        started = await next_event("chat_queue_execution_started")
        assert started["metadata"]["request_id"] == "selected"
        await next_event("chat_queue_execution_completed")
        assert graph.cancelled_updates == 1
        assert [bridge.message_id for bridge in bridges] == [selected_id]
        assert (await queue.snapshot("restart-send-now"))["messages"] == []
    finally:
        service._subscribers.discard(events)
        await service.stop()


async def test_responses_cancel_mid_stream_skips_checkpoint() -> None:
    loop_runner = FakeLoopRunner()
    gate = asyncio.Event()
    script = [
        _Wait(gate),
        {"type": "stream_chunk", "content": "should-not-arrive", "is_final": True},
    ]
    server = await _start_chat_server(loop_runner, bridge_factory=make_bridge_factory(script))
    task = None
    try:
        async with _client() as client:
            task = asyncio.create_task(
                _post_responses(client, server.base_url, "hi", "resp-cancel")
            )
            await asyncio.sleep(0.2)

            cancel_resp = await client.post(f"{server.base_url}/v1/responses/resp-cancel/cancel")
            assert cancel_resp.status_code == 200
            assert cancel_resp.json()["status"] == "cancelled"

            unknown_resp = await client.post(
                f"{server.base_url}/v1/responses/does-not-exist/cancel"
            )
            assert unknown_resp.status_code == 404
            assert unknown_resp.json()["error"]["code"] == "not_found"

            gate.set()
            frames = await asyncio.wait_for(task, timeout=5.0)
    finally:
        gate.set()
        if task is not None and not task.done():
            task.cancel()
        await _stop_chat_server(server)

    assert frames[-1][0] == "response.incomplete"
    assert frames[-1][1]["response"]["status"] == "cancelled"
    assert loop_runner.state_manager.saved_checkpoints == []


async def test_responses_bridge_cancellation_does_not_synthesize_completed() -> None:
    loop_runner = FakeLoopRunner()
    script = [
        {"type": "stream_chunk", "content": "partial", "is_final": False},
        _Raise(asyncio.CancelledError()),
    ]
    server = await _start_chat_server(loop_runner, bridge_factory=make_bridge_factory(script))
    try:
        async with _client() as client:
            frames = await _post_responses(client, server.base_url, "hi", "resp-bridge-cancel")
    finally:
        await _stop_chat_server(server)

    event_names = [n for n, _ in frames]
    assert event_names[0] == "response.created"
    assert event_names[1] == "response.in_progress"
    assert event_names[2] == "response.output_item.added"
    assert not any(name == "response.completed" for name, _ in frames)
    assert loop_runner.state_manager.saved_checkpoints == []


async def test_responses_writes_keepalive_comments_during_a_silent_bridge_gap(
    monkeypatch,
) -> None:
    monkeypatch.setattr("src.progena.runtime.http.chat.EVENTS_KEEPALIVE_SECONDS", 0.05)

    loop_runner = FakeLoopRunner()
    gate = asyncio.Event()
    script = [
        _Wait(gate),
        {"type": "stream_chunk", "content": "hello", "is_final": True},
    ]
    server = await _start_chat_server(loop_runner, bridge_factory=make_bridge_factory(script))
    try:
        async with (
            _client() as client,
            client.stream(
                "POST",
                f"{server.base_url}/v1/responses",
                json={
                    "input": "hi",
                    "stream": True,
                    "metadata": {"request_id": "resp-keepalive"},
                },
            ) as response,
        ):
            assert response.status_code == 200
            raw = b""
            gate_released = False
            async for chunk in response.aiter_bytes():
                raw += chunk
                if not gate_released and raw.count(b": keepalive\n\n") >= 2:
                    gate_released = True
                    gate.set()
                if b"event: response.completed\n" in raw:
                    break
    finally:
        gate.set()
        await _stop_chat_server(server)

    assert raw.count(b": keepalive\n\n") >= 2


async def test_direct_stream_queue_applies_backpressure(monkeypatch) -> None:
    """A slow response writer must bound how far the bridge producer gets ahead."""
    monkeypatch.setattr("src.progena.runtime.http.chat.DIRECT_STREAM_QUEUE_SIZE", 2)

    loop_runner = FakeLoopRunner()
    first_write_started = asyncio.Event()
    release_first_write = asyncio.Event()
    produced = 0

    class _FloodBridge:
        async def stream(
            self,
            _message: str,
            *,
            thread_id: str,
            received_at: float | None = None,
            attachments: list[dict[str, Any]] | None = None,
            source: str | None = None,
            message_id: str | None = None,
            metadata: dict[str, Any] | None = None,
        ):
            del thread_id, received_at, source
            nonlocal produced
            for index in range(30):
                produced += 1
                yield {"type": "stream_chunk", "content": str(index), "is_final": False}
            yield {"type": "stream_chunk", "content": "done", "is_final": True}

    class _SlowResponse:
        def __init__(self) -> None:
            self.writes: list[bytes] = []

        async def write(self, payload: bytes) -> None:
            self.writes.append(payload)
            if len(self.writes) == 1:
                first_write_started.set()
                await release_first_write.wait()

    service = RuntimeChatService(
        loop_runner,
        bridge_factory=lambda *_args, **_kwargs: _FloodBridge(),
    )
    response = _SlowResponse()
    stream_task = asyncio.create_task(
        service._stream_turn(
            response,
            "hello",
            "request-queue-bound",
            "thread-1",
            "m1",
            None,
            asyncio.Event(),
            123.0,
            0,
        )
    )
    try:
        await asyncio.wait_for(first_write_started.wait(), timeout=2.0)
        await asyncio.sleep(0)
        assert produced <= 4
        assert produced > 1
    finally:
        release_first_write.set()
        await asyncio.wait_for(stream_task, timeout=2.0)


async def test_responses_happy_path_runs_checkpoint() -> None:
    loop_runner = FakeLoopRunner()
    script = [{"type": "stream_chunk", "content": "ok", "is_final": True}]
    server = await _start_chat_server(loop_runner, bridge_factory=make_bridge_factory(script))
    try:
        async with _client() as client:
            frames = await _post_responses(client, server.base_url, "hi", "resp-checkpoint")
    finally:
        await _stop_chat_server(server)

    assert frames[-1][0] == "response.completed"
    assert len(loop_runner.state_manager.saved_checkpoints) == 1


async def test_responses_checkpoint_failure_emits_failed_not_completed() -> None:
    """A checkpoint failure must not be masked by an already-sent 'completed' frame."""
    loop_runner = FakeLoopRunner()

    async def failing_save_current_state(
        checkpoint: dict[str, Any], thread_id: str | None = None
    ) -> None:
        raise RuntimeError("disk full")

    loop_runner.state_manager.save_current_state = failing_save_current_state  # type: ignore[method-assign]

    script = [{"type": "stream_chunk", "content": "ok", "is_final": True}]
    server = await _start_chat_server(loop_runner, bridge_factory=make_bridge_factory(script))
    try:
        async with _client() as client:
            frames = await _post_responses(client, server.base_url, "hi", "resp-checkpoint-fail")
    finally:
        await _stop_chat_server(server)

    assert frames[-1][0] == "response.failed"
    assert frames[-1][1]["response"]["error"]["code"] == "checkpoint_error"
    assert "disk full" in frames[-1][1]["response"]["error"]["message"]
    assert not any(name == "response.completed" for name, _ in frames)


async def test_responses_bridge_error_emits_failed_and_releases_lock() -> None:
    loop_runner = FakeLoopRunner()
    script = {
        "boom": [_Raise(RuntimeError("kaboom"))],
        "ok": [{"type": "stream_chunk", "content": "fine", "is_final": True}],
    }
    server = await _start_chat_server(
        loop_runner, bridge_factory=make_bridge_factory(lambda msg: script[msg])
    )
    try:
        async with _client() as client:
            frames = await _post_responses(client, server.base_url, "boom", "resp-err")
            assert frames[-1][0] == "response.failed"
            assert frames[-1][1]["response"]["error"]["code"] == "stream_error"
            assert "kaboom" in frames[-1][1]["response"]["error"]["message"]

            frames_ok = await _post_responses(client, server.base_url, "ok", "resp-ok")
            assert frames_ok[-1][0] == "response.completed"
    finally:
        await _stop_chat_server(server)


async def test_responses_conversation_resolution() -> None:
    loop_runner = FakeLoopRunner(thread_id="state-thread")
    captured: list[ScriptedBridge] = []

    def factory(graph: Any, *, config: Any, state: dict[str, Any]) -> ScriptedBridge:
        bridge = ScriptedBridge(
            graph,
            config=config,
            state=state,
            script=lambda _msg: [{"type": "stream_chunk", "content": "x", "is_final": True}],
        )
        captured.append(bridge)
        return bridge

    server = await _start_chat_server(loop_runner, bridge_factory=factory)
    try:
        async with _client() as client:
            frames_a = await _post_responses(
                client, server.base_url, "hi", "resp-a", conversation=None
            )
            frames_b = await _post_responses(
                client, server.base_url, "hi", "resp-b", conversation=None
            )
            first, second = captured[0].thread_id, captured[1].thread_id
            # No conversation: a fresh isolated one each time, never the loop's
            # own thread, and reported back on the response.
            assert first.startswith("conv_") and second.startswith("conv_")
            assert first != second
            assert frames_a[-1][1]["response"]["conversation"] == {"id": first}
            assert frames_b[-1][1]["response"]["conversation"] == {"id": second}

            for conversation in ("named-thread", {"id": "named-thread"}):
                body = {"input": "hi", "stream": False, "conversation": conversation}
                resp = await client.post(f"{server.base_url}/v1/responses", json=body)
                assert resp.json()["conversation"] == {"id": "named-thread"}
                assert captured[-1].thread_id == "named-thread"
    finally:
        await _stop_chat_server(server)


async def test_responses_non_streaming_returns_completed() -> None:
    loop_runner = FakeLoopRunner()
    script = [{"type": "stream_chunk", "content": "yes", "is_final": True}]
    server = await _start_chat_server(loop_runner, bridge_factory=make_bridge_factory(script))
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{server.base_url}/v1/responses",
                json={
                    "input": "hi",
                    "stream": False,
                    "metadata": {"request_id": "resp-ns"},
                },
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body["status"] == "completed"
            assert body["id"] == "resp-ns"
            # Fake bridge does not add messages to the graph, so output is empty.
            assert body["output"] == []
    finally:
        await _stop_chat_server(server)


async def test_items_404_when_no_checkpoint() -> None:
    loop_runner = FakeLoopRunner(graph=_make_real_graph())
    server = await _start_chat_server(loop_runner)
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{server.base_url}/v1/conversations/no-such-thread/items")
            assert resp.status_code == 404
            assert resp.json()["error"]["code"] == "not_found"
    finally:
        await _stop_chat_server(server)


async def test_items_with_real_graph() -> None:
    graph = _make_real_graph()
    loop_runner = FakeLoopRunner(graph=graph)
    server = await _start_chat_server(loop_runner)
    try:
        # Seed the graph
        await graph.ainvoke(
            {"messages": [HumanMessage(content="hello", id="h1")]},
            {"configurable": {"thread_id": "t1"}},
        )

        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{server.base_url}/v1/conversations/t1/items?order=asc&limit=10"
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body["object"] == "list"
            assert len(body["data"]) == 2
            assert body["data"][0]["role"] == "user"
            assert body["data"][1]["role"] == "assistant"
            assert body["has_more"] is False
    finally:
        await _stop_chat_server(server)


async def test_items_without_summary_include_reads_one_graph_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = _make_real_graph()
    loop_runner = FakeLoopRunner(graph=graph)
    server = await _start_chat_server(loop_runner)

    async def _unexpected_history_load(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("ordinary items reads must not traverse checkpoint history")

    monkeypatch.setattr("src.progena.runtime.http.chat.load_conversation", _unexpected_history_load)
    try:
        await graph.ainvoke(
            {"messages": [HumanMessage(content="hello", id="h1")]},
            {"configurable": {"thread_id": "t1"}},
        )
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{server.base_url}/v1/conversations/t1/items?order=asc")
        assert response.status_code == 200
        assert [item["id"] for item in response.json()["data"]] == ["h1", "a1"]
    finally:
        await _stop_chat_server(server)


async def test_turn_threads_progena_source_from_metadata() -> None:
    loop_runner = FakeLoopRunner()
    bridges: list[Any] = []

    class _CapturingBridge:
        def __init__(self) -> None:
            self.last_source: str | None = None

        async def stream(
            self,
            message: str,
            *,
            thread_id: str,
            received_at: float,
            source: str | None = None,
            attachments: Any = None,
            message_id: str | None = None,
            metadata: dict[str, Any] | None = None,
        ) -> Any:
            self.last_source = source
            yield {"type": "stream_chunk", "content": "ok", "is_final": True}

    def _bridge_factory(*_args: Any, **_kwargs: Any) -> _CapturingBridge:
        b = _CapturingBridge()
        bridges.append(b)
        return b

    server = await _start_chat_server(loop_runner, bridge_factory=_bridge_factory)
    try:
        async with httpx.AsyncClient() as client:
            await _post_responses(
                client, server.base_url, "hi", "req-ui", metadata={"source": "ui"}
            )
        assert bridges[-1].last_source == "ui"

        async with httpx.AsyncClient() as client:
            await _post_responses(
                client, server.base_url, "hi", "req-api", metadata={"source": "api"}
            )
        assert bridges[-1].last_source == "api"

        async with httpx.AsyncClient() as client:
            await _post_responses(
                client, server.base_url, "hi", "req-wf", metadata={"source": "workflow"}
            )
        assert bridges[-1].last_source is None
    finally:
        await _stop_chat_server(server)


async def test_items_paging_and_invalid_limit() -> None:
    graph = _make_real_graph()
    loop_runner = FakeLoopRunner(graph=graph)
    server = await _start_chat_server(loop_runner)
    try:
        await graph.ainvoke(
            {"messages": [HumanMessage(content="a", id="h1")]},
            {"configurable": {"thread_id": "t1"}},
        )

        async with httpx.AsyncClient() as client:
            # limit too high
            resp = await client.get(f"{server.base_url}/v1/conversations/t1/items?limit=200")
            assert resp.status_code == 400

            # limit too low
            resp2 = await client.get(f"{server.base_url}/v1/conversations/t1/items?limit=0")
            assert resp2.status_code == 400
    finally:
        await _stop_chat_server(server)


async def test_delete_conversation_409_in_flight() -> None:
    loop_runner = FakeLoopRunner()
    gate = asyncio.Event()
    script = [_Wait(gate), {"type": "stream_chunk", "content": "x", "is_final": True}]
    server = await _start_chat_server(loop_runner, bridge_factory=make_bridge_factory(script))
    task = None
    try:
        async with _client() as client:
            task = asyncio.create_task(_post_responses(client, server.base_url, "hi", "resp-del"))
            await asyncio.sleep(0.2)

            resp = await client.delete(f"{server.base_url}/v1/conversations/{TEST_CONVERSATION}")
            assert resp.status_code == 409
            assert resp.json()["error"]["code"] == "conflict"

            gate.set()
            await asyncio.wait_for(task, timeout=5.0)
    finally:
        gate.set()
        if task is not None and not task.done():
            task.cancel()
        await _stop_chat_server(server)


async def test_delete_conversation_removes_checkpoints_and_runtime_state() -> None:
    graph = _make_real_graph()
    loop_runner = FakeLoopRunner(graph=graph)
    state_manager = StateManager(runtime_store=LangGraphRuntimeStore(InMemoryStore()))
    loop_runner.heartbeat_context = FakeHeartbeatContext(state_manager)  # type: ignore[arg-type]
    server = await _start_chat_server(loop_runner)
    try:
        await graph.ainvoke(
            {"messages": [HumanMessage(content="a", id="h1")]},
            {"configurable": {"thread_id": "t1"}},
        )
        await state_manager.update_current_state({"cycle_count": 3}, thread_id="t1")
        await state_manager.update_current_state({"cycle_count": 5}, thread_id="t2")

        async with httpx.AsyncClient() as client:
            resp = await client.delete(f"{server.base_url}/v1/conversations/t1")
        assert resp.status_code == 200
        assert resp.json() == {"id": "t1", "object": "conversation.deleted", "deleted": True}

        state = await graph.aget_state({"configurable": {"thread_id": "t1"}})
        assert state.created_at is None
        assert await state_manager.get_current_state(thread_id="t1") == {}
        assert await state_manager.get_current_state(thread_id="t2") == {"cycle_count": 5}
    finally:
        await _stop_chat_server(server)


async def test_events_broadcast_to_multiple_subscribers() -> None:
    loop_runner = FakeLoopRunner()
    server = await _start_chat_server(loop_runner, start_drain=True)
    try:
        async with _client() as client:

            async def _read_one_event(c: httpx.AsyncClient) -> tuple[str, dict[str, Any]]:
                async with c.stream("GET", f"{server.base_url}/v1/events") as resp:
                    assert resp.status_code == 200
                    event_name: str | None = None
                    async for line in resp.aiter_lines():
                        if line == "" or line.startswith(":"):
                            continue
                        if line.startswith("event: "):
                            event_name = line[len("event: ") :]
                            continue
                        if line.startswith("data: "):
                            assert event_name is not None
                            return event_name, json.loads(line[len("data: ") :])
                raise AssertionError("stream ended without an event")

            task_a = asyncio.create_task(_read_one_event(client))
            task_b = asyncio.create_task(_read_one_event(client))
            await asyncio.sleep(0.2)
            await loop_runner.queues.push_event("tick", "hello", {"n": 1})

            result_a = await asyncio.wait_for(task_a, timeout=5.0)
            result_b = await asyncio.wait_for(task_b, timeout=5.0)
    finally:
        await _stop_chat_server(server)

    assert result_a[0] == "agent_event"
    assert result_a[1]["event_type"] == "tick"
    assert result_b == result_a


async def test_events_slow_subscriber_does_not_block_fast_subscriber() -> None:
    loop_runner = FakeLoopRunner()
    service = RuntimeChatService(loop_runner)
    await service.start()
    try:
        fast_queue: asyncio.Queue = asyncio.Queue(maxsize=256)
        slow_queue: asyncio.Queue = asyncio.Queue(maxsize=2)
        service._subscribers.add(fast_queue)
        service._subscribers.add(slow_queue)

        for i in range(10):
            await loop_runner.queues.push_event("tick", str(i))

        await asyncio.sleep(0.3)

        assert fast_queue.qsize() == 10
        assert slow_queue.qsize() == 2
    finally:
        await service.stop()


_MODEL_INPUTS: list[list[Any]] = []


class _SlowStreamingModel(GenericFakeChatModel):
    """Fake chat model that streams word by word slowly and records its inputs."""

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):  # type: ignore[override]
        _MODEL_INPUTS.append(list(messages))
        async for chunk in super()._astream(messages, stop=stop, run_manager=run_manager, **kwargs):
            await asyncio.sleep(0.05)
            yield chunk


async def test_cancelled_turn_keeps_partial_reply_and_next_turn_continues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real create_agent graph + real SSE bridge: Stop keeps what was streamed."""
    repository = _ProviderRepository()
    monkeypatch.setattr(provider_runtime, "get_provider_repository", lambda: repository)
    _MODEL_INPUTS.clear()
    model = _SlowStreamingModel(
        messages=iter(
            [
                AIMessage(content=" ".join(f"word{i}" for i in range(200))),
                AIMessage(content="continued"),
            ]
        )
    )
    graph = create_agent(model=model, tools=[], checkpointer=InMemorySaver())
    loop_runner = FakeLoopRunner(graph=graph)
    server = await _start_chat_server(loop_runner, bridge_factory=DeepAgentSSEStream)
    config = {"configurable": {"thread_id": "c-cancel"}}
    try:
        async with _client() as client:
            frames: list[tuple[str, dict[str, Any]]] = []
            body = {
                "input": "write a lot",
                "stream": True,
                "conversation": "c-cancel",
                "metadata": {"request_id": "resp_c1"},
            }
            async with client.stream("POST", f"{server.base_url}/v1/responses", json=body) as resp:
                event_name = None
                async for line in resp.aiter_lines():
                    if line.startswith("event: "):
                        event_name = line[len("event: ") :]
                    elif line.startswith("data: "):
                        frames.append((str(event_name), json.loads(line[len("data: ") :])))
                        is_delta = event_name == "response.output_text.delta"
                        deltas = [f for f in frames if f[0] == "response.output_text.delta"]
                        if is_delta and len(deltas) == 3:
                            cancel = await client.post(
                                f"{server.base_url}/v1/responses/resp_c1/cancel"
                            )
                            assert cancel.status_code == 200
                        if event_name == "response.incomplete":
                            break

            streamed = "".join(
                f[1]["delta"] for f in frames if f[0] == "response.output_text.delta"
            )
            assert streamed.startswith("word0")
            final = frames[-1][1]["response"]
            assert final["status"] == "cancelled"
            [item] = final["output"]
            assert item["id"] == "msg_resp_c1"
            assert item["status"] == "incomplete"
            assert item["content"][0]["text"] == streamed

            # The thread now ends with the partial reply and has nothing pending.
            state = await graph.aget_state(config)
            assert state.next == ()
            items = await client.get(
                f"{server.base_url}/v1/conversations/c-cancel/items", params={"order": "asc"}
            )
            assert [(i["type"], i.get("role"), i["status"]) for i in items.json()["data"]] == [
                ("message", "user", "completed"),
                ("message", "assistant", "incomplete"),
            ]

            # The next turn runs fresh and sees the partial reply as context.
            resp = await client.post(
                f"{server.base_url}/v1/responses",
                json={"input": "continue", "conversation": "c-cancel"},
            )
            assert resp.json()["status"] == "completed"
            last_input = _MODEL_INPUTS[-1]
            assert [(m.type, m.content) for m in last_input if m.type != "system"] == [
                ("human", "write a lot"),
                ("ai", streamed),
                ("human", "continue"),
            ]
            state = await graph.aget_state(config)
            assert state.values["messages"][-1].content == "continued"
    finally:
        await _stop_chat_server(server)


async def test_session_renders_of_one_thread_run_in_order(monkeypatch) -> None:
    """A turn finishing mid-render queues one more render instead of racing it,
    so the last merge always reflects the last turn."""
    import src.progena.runtime.http.chat as http_chat

    service = RuntimeChatService(FakeLoopRunner())
    release = asyncio.Event()
    active = 0
    calls: list[str] = []

    async def fake_merge(thread_id: str, _checkpointer: Any, _state_manager: Any) -> None:
        nonlocal active
        active += 1
        assert active == 1, "renders of one thread overlapped"
        calls.append(thread_id)
        await release.wait()
        active -= 1

    monkeypatch.setattr(http_chat, "merge_session_after_turn", fake_merge)

    service._fire_and_forget_session_merge("t1")
    await asyncio.sleep(0)
    service._fire_and_forget_session_merge("t1")  # turn 2 finishes mid-render
    service._fire_and_forget_session_merge("t1")  # turn 3 too: coalesced
    release.set()
    task = service._session_renders["t1"]
    await asyncio.wait_for(task, 1)

    assert calls == ["t1", "t1"]
    assert service._session_renders == {}


async def test_http_turn_and_heartbeat_turn_share_compiled_graph_checkpoint_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A heartbeat callback waits for an active HTTP turn on the same thread."""
    monkeypatch.setattr(provider_runtime, "get_provider_repository", lambda: _ProviderRepository())
    http_started = asyncio.Event()
    heartbeat_started = asyncio.Event()
    release_http = asyncio.Event()
    active = 0
    max_active = 0

    class _State(TypedDict):
        messages: Annotated[list, add_messages]

    async def turn(state: _State) -> dict[str, list[AIMessage]]:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        try:
            content = str(state["messages"][-1].content)
            if content == "HTTP turn":
                http_started.set()
                await release_http.wait()
            else:
                heartbeat_started.set()
            return {"messages": [AIMessage(content=f"{content} complete")]}
        finally:
            active -= 1

    builder = StateGraph(_State)
    builder.add_node("turn", turn)
    builder.add_edge(START, "turn")
    builder.add_edge("turn", END)
    graph = builder.compile(checkpointer=InMemorySaver())
    loop_runner = FakeLoopRunner(graph=graph)
    loop_runner.heartbeat_context.agent_graph = graph
    loop_runner.heartbeat_context.llm_client = None
    server = await _start_chat_server(loop_runner, bridge_factory=DeepAgentSSEStream)
    try:
        async with _client() as client:
            http_turn = asyncio.create_task(
                client.post(
                    f"{server.base_url}/v1/responses",
                    json={
                        "input": "HTTP turn",
                        "stream": False,
                        "conversation": "shared",
                        "metadata": {"request_id": "resp-http"},
                    },
                )
            )
            await asyncio.wait_for(http_started.wait(), timeout=1)
            heartbeat_turn = asyncio.create_task(
                chat_node(
                    {"current_input": "heartbeat turn", "thread_id": "shared"},
                    Runtime(context=loop_runner.heartbeat_context),
                )
            )
            await asyncio.sleep(0)
            assert not heartbeat_started.is_set()

            release_http.set()
            http_response, _ = await asyncio.gather(http_turn, heartbeat_turn)

        assert http_response.status_code == 200
        assert http_response.json()["status"] == "completed"
        assert heartbeat_started.is_set()
        assert max_active == 1
    finally:
        release_http.set()
        await _stop_chat_server(server)
