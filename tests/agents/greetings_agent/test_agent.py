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


def test_greeting_styles_are_well_formed() -> None:
    names = [style for style, _phrase in agent_module._GREETING_STYLES]
    assert len(names) >= 5
    assert len(names) == len(set(names)), "duplicate greeting style names"
    for style, phrase in agent_module._GREETING_STYLES:
        assert style.strip() == style and style
        assert phrase.strip() == phrase and phrase


def test_pick_greeting_style_returns_one_of_the_known_styles() -> None:
    for _ in range(20):
        assert agent_module._pick_greeting_style() in agent_module._GREETING_STYLES


def test_pick_greeting_style_tool_is_registered() -> None:
    assert agent_module.pick_greeting_style in agent_module._TOOLS


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
async def test_task_history_matches_exact_transition_sequence_with_no_duplicates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: GreetingsExecutor used to read-modify-write the task
    store itself in _save_state() in addition to the framework's own
    TaskManager persisting every queued TaskStatusUpdateEvent, so each
    status message ended up appended to task.history twice. A multi-step
    request (several sequential progress transitions after the first) is
    what reliably exposed it: TaskManager caches its own in-memory Task
    after its first event and stops re-reading the store, so from the
    second transition onward it doesn't just duplicate but re-appends its
    own *stale* cached message instead of the new one."""
    monkeypatch.setenv("PROGENITOR_MOCK_LLM", "true")
    handler, store = _build_handler()

    result = await handler.on_message_send(
        MessageSendParams(
            message=_make_text_message("do a 3-step task: prep, build, verify"),
            configuration=MessageSendConfiguration(blocking=False),
        )
    )
    terminal = await _poll_terminal(handler, result.id)
    assert terminal.status.state == TaskState.completed

    saved = await store.get(result.id)
    assert saved is not None
    texts = [msg.parts[0].root.text for msg in (saved.history or [])]
    # Mock mode is fully deterministic, so the exact sequence is known --
    # note "Calling do_step..."/"do_step finished." legitimately repeats
    # once per step (see test_run_mock_steps_bookends_each_step_with_do_step_lifecycle),
    # so a same-text-appears-twice check can't tell that apart from the
    # duplicate-persistence bug this guards against; only an exact sequence can.
    assert texts == [
        "do a 3-step task: prep, build, verify",
        "Accepted: do a 3-step task: prep, build, verify",
        "Working on your greeting...",
        "Calling do_step...",
        "Step 1/3: prep...",
        "do_step finished.",
        "Calling do_step...",
        "Step 2/3: build...",
        "do_step finished.",
        "Calling do_step...",
        "Step 3/3: verify...",
        "do_step finished.",
    ]


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
async def test_multi_step_task_defaults_step_names_when_unnamed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PROGENITOR_MOCK_LLM", "true")
    handler, _store = _build_handler()

    result = await handler.on_message_send(
        MessageSendParams(
            message=_make_text_message("please do a 2-step task"),
            configuration=MessageSendConfiguration(blocking=False),
        )
    )

    terminal = await _poll_terminal(handler, result.id)
    assert terminal.status.state == TaskState.completed
    text = terminal.status.message.parts[0].root.text
    assert "step 1" in text
    assert "step 2" in text


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
    assert len(working_texts) >= 4


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


@pytest.mark.asyncio
async def test_mock_response_parse_table(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROGENITOR_MOCK_LLM", "true")
    monkeypatch.setattr(agent_module, "_probe_copilot", AsyncMock(return_value="hi from copilot"))

    assert (
        await agent_module._mock_response("tell me something")
        == "Hello from greetings_agent! You said: 'tell me something'"
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


@pytest.mark.asyncio
async def test_mock_response_recognizes_sleep_trigger_word_variants(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The system prompt promises "sleep, wait, or take your time for some number
    of seconds" as interchangeable trigger phrases -- each must actually reach
    the sleep branch in mock mode. Regression: the original regex required the
    literal word "sleep" immediately followed by whitespace and a digit, so it
    silently missed every other phrasing the prompt itself advertises, and even
    missed "sleepy" (the skill's own name) since the "y" isn't whitespace."""
    monkeypatch.setenv("PROGENITOR_MOCK_LLM", "true")
    phrases = [
        "sleep 0 seconds",
        "sleep for 0 seconds",
        "please wait 0 seconds then greet",
        "take your time, about 0 seconds",
        "pretend to work for 0 seconds",
        "test the sleepy reply for 0 seconds",
    ]
    for phrase in phrases:
        text = await agent_module._mock_response(phrase)
        assert text == "Hello! I pretended to work for 0 seconds.", phrase


@pytest.mark.asyncio
async def test_run_mock_sleep_reports_progress_once_per_chunk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A duration-driven request chains multiple "working" pings, the same way
    a multi-step request chains one per named step (see _run_mock_steps) --
    a second, duration-driven way to generate live progress events, alongside
    the step-count-driven one. asyncio.sleep is stubbed so this runs instantly
    instead of actually waiting out the chunked duration."""
    real_sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        real_sleeps.append(seconds)

    monkeypatch.setattr(agent_module.asyncio, "sleep", fake_sleep)
    calls: list[str] = []

    async def progress_callback(text: str) -> None:
        calls.append(text)

    await agent_module._run_mock_sleep(6.0, progress_callback)

    assert real_sleeps == [2.0, 2.0, 2.0]
    assert calls == [
        "Calling sleep...",
        "Still working... 2s elapsed of 6s total.",
        "Still working... 4s elapsed of 6s total.",
        "Still working... 6s elapsed of 6s total.",
        "sleep finished.",
    ]


@pytest.mark.asyncio
async def test_run_mock_sleep_with_duration_shorter_than_one_chunk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        real_sleeps.append(seconds)

    monkeypatch.setattr(agent_module.asyncio, "sleep", fake_sleep)
    calls: list[str] = []

    async def progress_callback(text: str) -> None:
        calls.append(text)

    await agent_module._run_mock_sleep(1.0, progress_callback)

    assert real_sleeps == [1.0]
    assert calls == [
        "Calling sleep...",
        "Still working... 1s elapsed of 1s total.",
        "sleep finished.",
    ]


@pytest.mark.asyncio
async def test_run_mock_sleep_with_zero_duration_reports_no_progress() -> None:
    calls: list[str] = []

    async def progress_callback(text: str) -> None:
        calls.append(text)

    await agent_module._run_mock_sleep(0.0, progress_callback)

    assert calls == []


@pytest.mark.asyncio
async def test_mock_response_bare_greeting_uses_a_random_style(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PROGENITOR_MOCK_LLM", "true")
    monkeypatch.setattr(
        agent_module, "_pick_greeting_style", lambda: ("Australian", "G'day, mate!")
    )

    for greeting in ("hi", "Hello there", "hey!", "Good morning", "howdy"):
        assert await agent_module._mock_response(greeting) == (
            f"Hello from greetings_agent! You said: {greeting!r}. "
            "Australian-style right back at you: G'day, mate!"
        )

    # Not anchored to the start of the message -> not treated as a bare greeting,
    # same precedence the sleep/copilot/steps checks above it already get.
    assert await agent_module._mock_response("thing hi") == (
        "Hello from greetings_agent! You said: 'thing hi'"
    )


@pytest.mark.asyncio
async def test_run_mock_steps_bookends_each_step_with_do_step_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: mock-mode progress used to emit only a generic "Step i/N"
    ping per step, not the "Calling do_step..." / "do_step finished." pair
    the real (non-mock) path emits for an actual do_step tool call -- so a
    multi-step mock run couldn't exercise the per-tool lifecycle
    display_tool_calls (see _run_greeting_payload) reports it as having."""
    monkeypatch.setattr(agent_module.asyncio, "sleep", AsyncMock())
    calls: list[str] = []

    async def progress_callback(text: str) -> None:
        calls.append(text)

    await agent_module._run_mock_steps(["prep", "build"], progress_callback)

    assert calls == [
        "Calling do_step...",
        "Step 1/2: prep...",
        "do_step finished.",
        "Calling do_step...",
        "Step 2/2: build...",
        "do_step finished.",
    ]


@pytest.mark.asyncio
async def test_mock_response_bare_greeting_reports_pick_greeting_style_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: the bare-greeting mock branch recorded pick_greeting_style
    in the final display_tool_calls metadata but never called
    progress_callback at all, so a live viewer saw no progress for it."""
    monkeypatch.setattr(
        agent_module, "_pick_greeting_style", lambda: ("Australian", "G'day, mate!")
    )
    calls: list[str] = []

    async def progress_callback(text: str) -> None:
        calls.append(text)

    await agent_module._mock_response("hello", progress_callback)

    assert calls == ["Calling pick_greeting_style...", "pick_greeting_style finished."]


@pytest.mark.asyncio
async def test_mock_response_copilot_branch_reports_probe_copilot_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same class of bug as the bare-greeting case: the copilot mock branch
    recorded probe_copilot in display_tool_calls but never reported progress."""
    monkeypatch.setattr(agent_module, "_probe_copilot", AsyncMock(return_value="hi there"))
    calls: list[str] = []

    async def progress_callback(text: str) -> None:
        calls.append(text)

    await agent_module._mock_response("ask copilot please", progress_callback)

    assert calls == ["Calling probe_copilot...", "probe_copilot finished."]


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


# ─── _report_message_progress against real (non-mock) LangChain shapes ────
#
# _report_message_progress reads duck-typed AIMessage/ToolMessage shapes from
# agent.astream(..., stream_mode="updates") -- the path a real (non-mock) LLM
# actually takes. Every other test in this file exercises PROGENITOR_MOCK_LLM
# mode instead, which never reaches this function's duck-typing at all, so
# these use the real langchain_core.messages classes to close that gap: a
# shape mismatch here degrades silently by design (best-effort, never breaks
# the greeting), so nothing else would catch a regression in the duck-typing
# itself.


@pytest.mark.asyncio
async def test_report_message_progress_relays_real_aimessage_tool_call() -> None:
    calls: list[str] = []

    async def progress_callback(text: str) -> None:
        calls.append(text)

    msg = AIMessage(
        content="",
        tool_calls=[
            {"name": "do_step", "args": {"step_number": 1}, "id": "call_1", "type": "tool_call"}
        ],
    )
    await agent_module._report_message_progress(msg, progress_callback)

    assert calls == ["Calling do_step..."]


@pytest.mark.asyncio
async def test_report_message_progress_relays_multiple_real_tool_calls_in_order() -> None:
    calls: list[str] = []

    async def progress_callback(text: str) -> None:
        calls.append(text)

    msg = AIMessage(
        content="",
        tool_calls=[
            {"name": "do_step", "args": {"step_number": 1}, "id": "call_1", "type": "tool_call"},
            {"name": "do_step", "args": {"step_number": 2}, "id": "call_2", "type": "tool_call"},
        ],
    )
    await agent_module._report_message_progress(msg, progress_callback)

    assert calls == ["Calling do_step...", "Calling do_step..."]


@pytest.mark.asyncio
async def test_report_message_progress_ignores_real_aimessage_with_no_tool_calls() -> None:
    calls: list[str] = []

    async def progress_callback(text: str) -> None:
        calls.append(text)

    msg = AIMessage(content="Hello! Nice to meet you.")
    await agent_module._report_message_progress(msg, progress_callback)

    assert calls == []


@pytest.mark.asyncio
async def test_report_message_progress_relays_real_toolmessage_completion() -> None:
    calls: list[str] = []

    async def progress_callback(text: str) -> None:
        calls.append(text)

    msg = ToolMessage(
        content="Step 1 (prep) done after 1.0s.", name="do_step", tool_call_id="call_1"
    )
    await agent_module._report_message_progress(msg, progress_callback)

    assert calls == ["do_step finished."]


@pytest.mark.asyncio
async def test_report_message_progress_is_a_noop_with_no_progress_callback() -> None:
    # Must not raise -- _run_deep_agent always calls this even when
    # progress_callback is None (the non-streaming-consumer case).
    msg = AIMessage(
        content="",
        tool_calls=[{"name": "do_step", "args": {}, "id": "call_1", "type": "tool_call"}],
    )
    await agent_module._report_message_progress(msg, None)


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
