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
from langchain_core.messages import AIMessage, ToolMessage

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
    executor = GreetingsExecutor(push_sender=push_sender)
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
    assert AGENT_CARD.capabilities.streaming is True
    assert AGENT_CARD.capabilities.state_transition_history is True
    assert [skill.id for skill in AGENT_CARD.skills] == [
        "greet",
        "sleepy_greeting",
        "copilot_probe",
        "multi_step_task",
        "cultural_greeting",
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
async def test_bare_greeting_calls_pick_greeting_style(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROGENITOR_MOCK_LLM", "true")
    monkeypatch.setattr(
        agent_module, "_pick_greeting_style", lambda: ("Irish", "Top o' the mornin'!")
    )
    handler, _store = _build_handler()

    result = await handler.on_message_send(
        MessageSendParams(
            message=_make_text_message("Hi"),
            configuration=MessageSendConfiguration(blocking=False),
        )
    )

    terminal = await _poll_terminal(handler, result.id)
    assert terminal.status.state == TaskState.completed
    text = terminal.status.message.parts[0].root.text
    assert "Irish-style right back at you: Top o' the mornin'!" in text
    assert terminal.status.message.metadata["display_tool_calls"] == ["pick_greeting_style"]


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
async def test_push_notification_not_sent_twice_per_transition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: GreetingsExecutor used to call self._notify() itself after
    every enqueue_event(), duplicating the push notification
    DefaultRequestHandler already sends via the same push_sender once it
    processes that queued event -- each transition must be delivered once,
    not twice."""
    monkeypatch.setenv("PROGENITOR_MOCK_LLM", "true")
    push_sender = AsyncMock()
    handler, _store = _build_handler(push_sender=push_sender)

    # Not a bare greeting (not anchored at the start, see the "thing hi"
    # case in test_mock_response_bare_greeting_uses_a_random_style), not a
    # steps/sleep/copilot trigger either -- the generic fallback branch,
    # which reports no tool calls and so never invokes progress_callback.
    # Keeps this test's expected count independent of how many progress
    # transitions any particular mock branch happens to emit.
    result = await handler.on_message_send(
        MessageSendParams(
            message=_make_text_message("thing hi"),
            configuration=MessageSendConfiguration(blocking=False),
        )
    )
    terminal = await _poll_terminal(handler, result.id)
    assert terminal.status.state == TaskState.completed

    # submitted, working, completed -- exactly one notification per
    # transition, not two.
    assert push_sender.send_notification.await_count == 3


@pytest.mark.asyncio
async def test_empty_request_fails_fast() -> None:
    queue = AsyncMock(spec=EventQueue)
    queue.enqueue_event = AsyncMock()
    context = AsyncMock()
    context.task_id = "empty-1"
    context.context_id = "ctx-1"
    context.message = _make_text_message("   ")

    await GreetingsExecutor().execute(context, queue)

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
async def test_multi_step_task_calls_do_step_per_step(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROGENITOR_MOCK_LLM", "true")
    handler, _store = _build_handler()

    result = await handler.on_message_send(
        MessageSendParams(
            message=_make_text_message("do a 3-step task: prep, build, verify"),
            configuration=MessageSendConfiguration(blocking=False),
        )
    )

    terminal = await _poll_terminal(handler, result.id)
    assert terminal.status.state == TaskState.completed
    text = terminal.status.message.parts[0].root.text
    assert "prep" in text
    assert "build" in text
    assert "verify" in text
    assert terminal.status.message.metadata["display_tool_calls"] == [
        "do_step",
        "do_step",
        "do_step",
    ]


@pytest.mark.asyncio
async def test_steps_request_that_also_contains_a_sleep_trigger_word_reports_only_do_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: _mock_response only ever runs one branch (steps takes
    precedence over sleep, see its own if/return chain), but the display
    metadata used to derive tool_calls via independent ifs rather than
    mirroring that precedence -- a steps request whose text also happened to
    contain a sleep trigger word (much easier to hit now that _SLEEP_RE
    matches "wait"/"take your time"/etc, not just the literal word "sleep")
    reported a phantom "sleep" call for a phase that never actually ran."""
    monkeypatch.setenv("PROGENITOR_MOCK_LLM", "true")
    handler, _store = _build_handler()

    result = await handler.on_message_send(
        MessageSendParams(
            message=_make_text_message(
                "do a 2-step task: prep, verify -- take your time, about 0 seconds"
            ),
            configuration=MessageSendConfiguration(blocking=False),
        )
    )

    terminal = await _poll_terminal(handler, result.id)
    assert terminal.status.state == TaskState.completed
    assert terminal.status.message.metadata["display_tool_calls"] == ["do_step", "do_step"]


@pytest.mark.asyncio
async def test_streaming_delivers_live_progress_events_in_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole point of running execute() inline: a message/stream caller sees each
    step as its own live working event, not just a submitted -> completed jump. This is
    what AgentCapabilities.streaming=True actually being usable depends on."""
    monkeypatch.setenv("PROGENITOR_MOCK_LLM", "true")
    handler, _store = _build_handler()

    events = []
    async for event in handler.on_message_send_stream(
        MessageSendParams(
            message=_make_text_message("do a 3-step task: alpha, beta, gamma"),
            configuration=MessageSendConfiguration(blocking=False),
        )
    ):
        events.append(event)

    states = [e.status.state for e in events]
    assert states[0] == TaskState.submitted
    assert states[-1] == TaskState.completed

    working_texts = [
        e.status.message.parts[0].root.text
        for e in events
        if e.status.state == TaskState.working and e.status.message
    ]
    # One kickoff push, then one per step naming that step -- proves genuinely
    # incremental, per-step delivery, not a single generic "working" blip.
    assert any("alpha" in t for t in working_texts)
    assert any("beta" in t for t in working_texts)
    assert any("gamma" in t for t in working_texts)


@pytest.mark.asyncio
async def test_cancel_delivers_terminal_event_to_live_stream_watcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A caller actively watching the original message/stream connection must see a
    live canceled/final=True event -- not just have on_cancel_task's own RPC response
    and task_store reflect it. on_cancel_task taps a fresh queue purely for its own
    validation; pushing only to that tap does not by itself reach whoever is watching
    the original stream (tapping is a one-way parent -> child forward, not shared
    between sibling taps)."""
    monkeypatch.setenv("PROGENITOR_MOCK_LLM", "true")
    handler, _store = _build_handler()

    events: list = []
    task_id_holder: dict[str, str] = {}

    async def _consume_stream() -> None:
        async for event in handler.on_message_send_stream(
            MessageSendParams(
                message=_make_text_message("sleep 5 then greet"),
                configuration=MessageSendConfiguration(blocking=False),
            )
        ):
            events.append(event)
            task_id_holder.setdefault("id", event.task_id)

    consumer_task = asyncio.create_task(_consume_stream())
    for _ in range(200):
        if "id" in task_id_holder:
            break
        await asyncio.sleep(0.01)
    assert "id" in task_id_holder, "stream never delivered even the submitted event"

    await handler.on_cancel_task(TaskIdParams(id=task_id_holder["id"]))
    await asyncio.wait_for(consumer_task, timeout=2.0)

    states = [e.status.state for e in events]
    assert states[-1] == TaskState.canceled
    assert events[-1].final is True


@pytest.mark.asyncio
async def test_concurrent_cancel_calls_deliver_only_one_terminal_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: on_cancel_task (a2a-sdk's DefaultRequestHandler) does not
    serialize concurrent cancel requests for the same task -- it only checks
    the task_store's current state, then calls straight into
    GreetingsExecutor.cancel(). Two such calls racing in before either has
    written a terminal state used to both observe worker.done() is False and
    both push a terminal event to the live stream watcher, delivering it
    twice. cancel() now claims the worker with an atomic pop so only the
    first of the two proceeds.
    """
    monkeypatch.setenv("PROGENITOR_MOCK_LLM", "true")
    handler, _store = _build_handler()

    events: list = []
    task_id_holder: dict[str, str] = {}

    async def _consume_stream() -> None:
        async for event in handler.on_message_send_stream(
            MessageSendParams(
                message=_make_text_message("sleep 5 then greet"),
                configuration=MessageSendConfiguration(blocking=False),
            )
        ):
            events.append(event)
            task_id_holder.setdefault("id", event.task_id)

    consumer_task = asyncio.create_task(_consume_stream())
    for _ in range(200):
        if "id" in task_id_holder:
            break
        await asyncio.sleep(0.01)
    assert "id" in task_id_holder, "stream never delivered even the submitted event"

    await asyncio.gather(
        handler.on_cancel_task(TaskIdParams(id=task_id_holder["id"])),
        handler.on_cancel_task(TaskIdParams(id=task_id_holder["id"])),
    )
    await asyncio.wait_for(consumer_task, timeout=2.0)

    canceled_final_events = [
        e for e in events if e.status.state == TaskState.canceled and e.final is True
    ]
    assert len(canceled_final_events) == 1


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


# ─── _run_deep_agent plumbing against a realistic (not mock) stream shape ──
#
# Exercises _run_deep_agent's own astream(..., stream_mode=["updates",
# "values"]) handling end to end -- not just the leaf _report_message_progress
# function above -- against a fake agent that yields the same shape a real
# deepagents/LangGraph agent does (real AIMessage/ToolMessage instances keyed
# under a node name, plus a final "values" snapshot), without needing actual
# model credentials.


class _FakeStreamingDeepAgent:
    """Mimics a LangGraph-compiled agent's .astream(..., stream_mode=[...])
    shape: an async generator of (mode, payload) tuples, "updates" keyed by
    node name, "values" carrying the accumulated final state."""

    def __init__(self, events: list[tuple[str, dict]]) -> None:
        self._events = events

    async def astream(self, _input_state, stream_mode):
        assert stream_mode == ["updates", "values"]
        for mode, payload in self._events:
            yield mode, payload


@pytest.mark.asyncio
async def test_run_deep_agent_relays_progress_from_a_realistic_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_agent = _FakeStreamingDeepAgent(
        [
            (
                "updates",
                {
                    "model": {
                        "messages": [
                            AIMessage(
                                content="",
                                tool_calls=[
                                    {
                                        "name": "do_step",
                                        "args": {},
                                        "id": "call_1",
                                        "type": "tool_call",
                                    }
                                ],
                            )
                        ]
                    }
                },
            ),
            (
                "updates",
                {
                    "tools": {
                        "messages": [
                            ToolMessage(
                                content="Step 1 done.", name="do_step", tool_call_id="call_1"
                            )
                        ]
                    }
                },
            ),
            ("values", {"messages": [AIMessage(content="Hello! All done.")]}),
        ]
    )
    monkeypatch.setattr(agent_module, "_get_deep_agent", AsyncMock(return_value=fake_agent))

    calls: list[str] = []

    async def progress_callback(text: str) -> None:
        calls.append(text)

    result = await agent_module._run_deep_agent("do a step", progress_callback)

    assert calls == ["Calling do_step...", "do_step finished."]
    assert agent_module._extract_text(result) == "Hello! All done."


@pytest.mark.asyncio
async def test_run_deep_agent_ignores_non_dict_updates_payloads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Defensive: stream_mode=["updates", "values"] always pairs a mode string
    with its matching payload shape in practice, but _run_deep_agent guards
    both isinstance checks explicitly -- confirm a malformed "updates" payload
    is skipped rather than raising, same best-effort contract as
    _report_message_progress itself."""
    fake_agent = _FakeStreamingDeepAgent(
        [
            ("updates", "not-a-dict"),
            ("updates", {"model": {"messages": "not-a-list"}}),
            ("values", {"messages": [AIMessage(content="Hello!")]}),
        ]
    )
    monkeypatch.setattr(agent_module, "_get_deep_agent", AsyncMock(return_value=fake_agent))

    result = await agent_module._run_deep_agent("hi", None)

    assert agent_module._extract_text(result) == "Hello!"
