"""Regression tests for SSE subscriber eviction and background-task retention.

A full subscriber queue means its stream handler stopped draining — typically
because it is blocked writing to a dead or half-open socket. ``broadcast_ui_event``
must evict that subscriber AND cancel its handler task so the HTTP connection
actually closes; otherwise the client's EventSource stays open-but-deaf forever.
Healthy subscribers in the same broadcast must still receive the envelope.

``_spawn_background_task`` must hold a strong reference to fire-and-forget
tasks while they run (the event loop only keeps weak references, so an
unreferenced task can be garbage-collected mid-run) and drop the reference
once the task completes, whether it succeeded or raised.
"""

import asyncio

import pytest

import src.backend.chat.responses as chat_responses
import src.backend.events.stream as events_stream


@pytest.fixture
def isolated_subscribers():
    """Run a test against an empty subscriber registry, restoring prior state."""
    original_subscribers = dict(events_stream._ui_stream_subscribers)
    original_replay = list(events_stream._ui_event_replay)
    events_stream._ui_stream_subscribers.clear()
    try:
        yield events_stream._ui_stream_subscribers
    finally:
        events_stream._ui_stream_subscribers.clear()
        events_stream._ui_stream_subscribers.update(original_subscribers)
        events_stream._ui_event_replay[:] = original_replay


def _full_queue() -> asyncio.Queue:
    """A maxsize-1 queue pre-filled to capacity: its handler stopped draining."""
    queue: asyncio.Queue = asyncio.Queue(maxsize=1)
    queue.put_nowait({"type": "ui.event", "envelope": None})
    return queue


async def _assert_discarded(task: asyncio.Task) -> None:
    """Done callbacks run via loop.call_soon; give the loop a few cycles."""
    for _ in range(10):
        if task not in chat_responses._background_tasks:
            return
        await asyncio.sleep(0)
    raise AssertionError("completed task still strongly referenced in _background_tasks")


class TestBroadcastEviction:
    async def test_eviction_cancels_stalled_handler_and_still_delivers_to_healthy(
        self, isolated_subscribers
    ):
        stalled_queue = _full_queue()
        handler = asyncio.create_task(asyncio.Event().wait())
        healthy_queue: asyncio.Queue = asyncio.Queue()
        isolated_subscribers[stalled_queue] = handler
        isolated_subscribers[healthy_queue] = None

        envelope = events_stream.broadcast_ui_event("test.event", {})

        # The stalled subscriber is evicted; the healthy one survives.
        assert stalled_queue not in isolated_subscribers
        assert healthy_queue in isolated_subscribers

        # Its handler task is cancelled so the HTTP connection closes.
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(handler, timeout=1)
        assert handler.cancelled()

        # The same broadcast still reaches the healthy subscriber.
        event = healthy_queue.get_nowait()
        assert event["type"] == "ui.event"
        assert event["envelope"] is envelope
        assert envelope.type == "test.event"

    async def test_eviction_tolerates_subscriber_without_handler_task(self, isolated_subscribers):
        stalled_queue = _full_queue()
        isolated_subscribers[stalled_queue] = None

        events_stream.broadcast_ui_event("test.event", {})

        assert stalled_queue not in isolated_subscribers

    async def test_eviction_tolerates_already_finished_handler_task(self, isolated_subscribers):
        finished = asyncio.create_task(asyncio.sleep(0))
        await finished
        stalled_queue = _full_queue()
        isolated_subscribers[stalled_queue] = finished

        events_stream.broadcast_ui_event("test.event", {})

        assert stalled_queue not in isolated_subscribers
        assert not finished.cancelled()


class TestSpawnBackgroundTask:
    async def test_task_is_strongly_referenced_while_suspended_then_discarded(self):
        started = asyncio.Event()
        release = asyncio.Event()

        async def worker() -> str:
            started.set()
            await release.wait()
            return "done"

        task = chat_responses._spawn_background_task(worker())
        try:
            # Referenced from the moment it is spawned...
            assert task in chat_responses._background_tasks
            await asyncio.wait_for(started.wait(), timeout=1)
            # ...and still referenced while the coroutine is suspended.
            assert task in chat_responses._background_tasks
        finally:
            release.set()

        assert await asyncio.wait_for(task, timeout=1) == "done"
        await _assert_discarded(task)

    async def test_failed_task_is_discarded_after_exception(self):
        async def failing() -> None:
            await asyncio.sleep(0)
            raise RuntimeError("boom")

        task = chat_responses._spawn_background_task(failing())
        assert task in chat_responses._background_tasks

        with pytest.raises(RuntimeError, match="boom"):
            await asyncio.wait_for(task, timeout=1)
        await _assert_discarded(task)
