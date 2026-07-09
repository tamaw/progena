from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock

import httpx
import pytest
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import (
    BasePushNotificationSender,
    InMemoryPushNotificationConfigStore,
    InMemoryTaskStore,
)
from a2a.types import (
    Message,
    MessageSendConfiguration,
    MessageSendParams,
    Part,
    PushNotificationConfig,
    Role,
    TaskIdParams,
    TaskQueryParams,
    TaskState,
    TextPart,
)

from app.agents.greetings_agent import agent as agent_module
from app.agents.greetings_agent.agent import AGENT_CARD, GreetingsExecutor, _normalize_base_url


def _make_text_message(text: str) -> Message:
    return Message(
        message_id="msg-1",
        role=Role.user,
        parts=[Part(root=TextPart(text=text))],
    )


def _build_handler(
    *,
    store: InMemoryTaskStore | None = None,
    push_sender: BasePushNotificationSender | AsyncMock | None = None,
    push_config_store: InMemoryPushNotificationConfigStore | None = None,
) -> tuple[DefaultRequestHandler, InMemoryTaskStore]:
    task_store = store or InMemoryTaskStore()
    config_store = push_config_store or InMemoryPushNotificationConfigStore()
    executor = GreetingsExecutor(task_store=task_store, push_sender=push_sender)
    handler = DefaultRequestHandler(
        agent_executor=executor,
        task_store=task_store,
        push_config_store=config_store,
        push_sender=push_sender,
    )
    return handler, task_store


async def _poll_terminal(handler: DefaultRequestHandler, task_id: str) -> object:
    latest = None
    for _ in range(300):
        latest = await handler.on_get_task(TaskQueryParams(id=task_id))
        if latest is not None and latest.status.state in {
            TaskState.completed,
            TaskState.failed,
            TaskState.canceled,
        }:
            return latest
        await asyncio.sleep(0.01)
    raise AssertionError(f"Task {task_id} did not reach terminal state; latest={latest!r}")


@pytest.mark.asyncio
async def test_agent_card_matches_contract() -> None:
    assert AGENT_CARD.name == "greetings_agent"
    assert ":8109" in AGENT_CARD.url
    assert AGENT_CARD.capabilities.push_notifications is True
    assert AGENT_CARD.capabilities.streaming is False
    assert AGENT_CARD.capabilities.state_transition_history is True
    assert [skill.id for skill in AGENT_CARD.skills] == [
        "greet",
        "sleepy_greeting",
        "copilot_probe",
    ]


@pytest.mark.asyncio
async def test_mock_end_to_end_greeting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROGENITOR_MOCK_LLM", "true")
    handler, _store = _build_handler()

    result = await handler.on_message_send(
        MessageSendParams(
            message=_make_text_message("Hi there"),
            configuration=MessageSendConfiguration(blocking=False),
        )
    )

    assert result.status.state == TaskState.submitted
    terminal = await _poll_terminal(handler, result.id)
    assert terminal.status.state == TaskState.completed
    assert "Hello from greetings_agent!" in terminal.status.message.parts[0].root.text
    assert terminal.status.message.metadata["display_agent_name"] == "greetings_agent"


@pytest.mark.asyncio
async def test_sleep_async_transitions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROGENITOR_MOCK_LLM", "true")
    handler, _store = _build_handler()

    started = time.perf_counter()
    result = await handler.on_message_send(
        MessageSendParams(
            message=_make_text_message("please sleep 1 second"),
            configuration=MessageSendConfiguration(blocking=False),
        )
    )

    saw_working = False
    while time.perf_counter() - started < 2.0:
        latest = await handler.on_get_task(TaskQueryParams(id=result.id))
        if latest is not None and latest.status.state == TaskState.working:
            saw_working = True
            break
        await asyncio.sleep(0.02)

    terminal = await _poll_terminal(handler, result.id)
    elapsed = time.perf_counter() - started

    assert saw_working is True
    assert terminal.status.state == TaskState.completed
    assert elapsed >= 1.0


@pytest.mark.asyncio
async def test_failure_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROGENITOR_MOCK_LLM", "true")
    handler, _store = _build_handler()

    result = await handler.on_message_send(
        MessageSendParams(
            message=_make_text_message("please fail now"),
            configuration=MessageSendConfiguration(blocking=False),
        )
    )

    terminal = await _poll_terminal(handler, result.id)
    assert terminal.status.state == TaskState.failed
    assert (
        "Intentional failure requested by mock input" in terminal.status.message.parts[0].root.text
    )


@pytest.mark.asyncio
async def test_copilot_probe_monkeypatched(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROGENITOR_MOCK_LLM", "true")
    probe = AsyncMock(return_value="Hello back from Copilot!")
    monkeypatch.setattr(agent_module, "_probe_copilot", probe)
    handler, _store = _build_handler()

    result = await handler.on_message_send(
        MessageSendParams(
            message=_make_text_message("please ask copilot to say hello"),
            configuration=MessageSendConfiguration(blocking=False),
        )
    )

    terminal = await _poll_terminal(handler, result.id)
    assert terminal.status.state == TaskState.completed
    assert "Hello back from Copilot!" in terminal.status.message.parts[0].root.text
    assert "probe_copilot" in terminal.status.message.metadata["display_tool_calls"]
    probe.assert_awaited_once()


@pytest.mark.asyncio
async def test_push_notification_sent_on_completion(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROGENITOR_MOCK_LLM", "true")
    captured: list[bytes] = []

    async def _handler(request: httpx.Request) -> httpx.Response:
        captured.append(await request.aread())
        return httpx.Response(204)

    transport = httpx.MockTransport(_handler)
    http_client = httpx.AsyncClient(transport=transport, timeout=5.0)
    config_store = InMemoryPushNotificationConfigStore()
    push_sender = BasePushNotificationSender(http_client, config_store)
    handler, _store = _build_handler(push_sender=push_sender, push_config_store=config_store)

    try:
        result = await handler.on_message_send(
            MessageSendParams(
                message=_make_text_message("Hi there"),
                configuration=MessageSendConfiguration(
                    blocking=False,
                    push_notification_config=PushNotificationConfig(
                        url="http://callback.test/a2a/tasks/callback"
                    ),
                ),
            )
        )
        terminal = await _poll_terminal(handler, result.id)
        assert terminal.status.state == TaskState.completed

        assert captured
        payload = httpx.Response(200, content=captured[-1]).json()
        assert payload["kind"] == "task"
        assert payload["status"]["state"] == "completed"
    finally:
        await http_client.aclose()


@pytest.mark.asyncio
async def test_empty_request_fails_fast() -> None:
    queue = AsyncMock(spec=EventQueue)
    queue.enqueue_event = AsyncMock()
    context = AsyncMock()
    context.task_id = "empty-1"
    context.context_id = "ctx-1"
    context.message = _make_text_message("   ")

    await GreetingsExecutor(task_store=InMemoryTaskStore()).execute(context, queue)

    assert queue.enqueue_event.await_count == 1
    event = queue.enqueue_event.await_args.args[0]
    assert event.final is True
    assert event.status.state == TaskState.failed


@pytest.mark.asyncio
async def test_cancel_marks_canceled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROGENITOR_MOCK_LLM", "true")
    handler, store = _build_handler()

    result = await handler.on_message_send(
        MessageSendParams(
            message=_make_text_message("sleep 5 then greet"),
            configuration=MessageSendConfiguration(blocking=False),
        )
    )

    canceled = await handler.on_cancel_task(TaskIdParams(id=result.id))
    assert canceled.status.state == TaskState.canceled

    terminal = await _poll_terminal(handler, result.id)
    assert terminal.status.state == TaskState.canceled

    saved = await store.get(result.id)
    assert saved is not None
    assert saved.status.state == TaskState.canceled


@pytest.mark.asyncio
async def test_mock_response_parse_table(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROGENITOR_MOCK_LLM", "true")
    monkeypatch.setattr(agent_module, "_probe_copilot", AsyncMock(return_value="hi from copilot"))

    assert (
        await agent_module._mock_response("hello there")
        == "Hello from greetings_agent! You said: 'hello there'"
    )
    assert (
        await agent_module._mock_response("sleep 0 seconds")
        == "Hello! I pretended to work for 0 seconds."
    )
    assert (
        await agent_module._mock_response("ask copilot please") == "Copilot says: hi from copilot"
    )

    with pytest.raises(RuntimeError, match="Intentional failure"):
        await agent_module._mock_response("please fail now")


def test_normalize_base_url_cases() -> None:
    assert (
        _normalize_base_url("https://example.test/openai/v1/responses")
        == "https://example.test/openai/v1"
    )
    assert (
        _normalize_base_url("https://example.test/openai/v1/responses?api-version=2025-01-01")
        == "https://example.test/openai/v1"
    )
    assert (
        _normalize_base_url("https://example.test/openai/v1/") == "https://example.test/openai/v1"
    )
