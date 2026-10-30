"""RuntimeConversationReader availability and caching behaviour.

Lossless message reconstruction against the real runtime is covered end to end
in ``test_sse_client.py``; these tests drive the reader with an
``httpx.MockTransport`` to control outages precisely.
"""

from __future__ import annotations

import time
from typing import Any

import httpx
import pytest
from langchain_core.messages import AIMessage, HumanMessage, messages_to_dict

from src.backend.integrations import runtime_history as runtime_conversations
from src.backend.integrations.runtime_history import RuntimeConversationReader, RuntimeUnavailable

MESSAGES = [HumanMessage(content="hi", id="h1"), AIMessage(content="hello", id="a1")]


def _items_page() -> dict[str, Any]:
    return {
        "object": "list",
        "data": [],
        "has_more": False,
        "progena_langchain_messages": messages_to_dict(MESSAGES),
    }


class _Runtime:
    """Mock runtime that can be taken down and brought back."""

    def __init__(self) -> None:
        self.up = True
        self.status = 200
        self.calls = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        if not self.up:
            raise httpx.ConnectError("connection refused", request=request)
        if self.status != 200:
            return httpx.Response(self.status, json={"error": {"message": "nope"}})
        return httpx.Response(200, json=_items_page())


def _reader(runtime: _Runtime) -> RuntimeConversationReader:
    reader = RuntimeConversationReader("http://runtime")
    reader._client = httpx.Client(
        base_url="http://runtime", transport=httpx.MockTransport(runtime.handler)
    )
    return reader


def test_checkpoint_reads_are_cached() -> None:
    runtime = _Runtime()
    reader = _reader(runtime)

    first = reader.get_messages("t1", "cp1")
    second = reader.get_messages("t1", "cp1")

    assert messages_to_dict(first) == messages_to_dict(MESSAGES) == messages_to_dict(second)
    assert runtime.calls == 1


def test_unknown_conversation_has_no_messages() -> None:
    runtime = _Runtime()
    runtime.status = 404
    assert _reader(runtime).get_messages("missing", None) == []


def test_runtime_error_status_is_unavailable_not_empty() -> None:
    runtime = _Runtime()
    runtime.status = 500
    with pytest.raises(RuntimeUnavailable):
        _reader(runtime).get_messages("t1", "cp1")


def test_outage_serves_last_known_copy_then_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = _Runtime()
    reader = _reader(runtime)
    reader.get_messages("t1", "cp1")

    runtime.up = False
    # A newer checkpoint can't be fetched: the last copy seen is served instead.
    stale = reader.get_messages("t1", "cp2")
    assert messages_to_dict(stale) == messages_to_dict(MESSAGES)
    # A thread never seen before has nothing to fall back on.
    with pytest.raises(RuntimeUnavailable):
        reader.get_messages("t2", "cp1")
    calls_during_outage = runtime.calls

    # Within the cooldown the runtime is not called at all.
    with pytest.raises(RuntimeUnavailable):
        reader.get_messages("t3", "cp1")
    assert runtime.calls == calls_during_outage

    # After the cooldown, a recovered runtime is used again.
    runtime.up = True
    now = time.monotonic()
    monkeypatch.setattr(
        runtime_conversations.time,
        "monotonic",
        lambda: now + runtime_conversations.UNAVAILABLE_COOLDOWN_SECONDS + 1,
    )
    assert messages_to_dict(reader.get_messages("t3", "cp1")) == messages_to_dict(MESSAGES)


def test_exact_checkpoint_read_never_substitutes_another_revision() -> None:
    runtime = _Runtime()
    reader = _reader(runtime)
    reader.get_messages("t1", "cp1")

    runtime.up = False
    with pytest.raises(RuntimeUnavailable):
        reader.get_messages("t1", "cp2", exact_revision=True)


def test_missing_native_message_projection_is_unavailable_not_empty() -> None:
    reader = RuntimeConversationReader("http://runtime")
    reader.close()
    reader._client = httpx.Client(
        base_url="http://runtime",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"data": []})),
    )
    try:
        with pytest.raises(RuntimeUnavailable):
            reader.get_messages("t1", "cp1", exact_revision=True)
    finally:
        reader.close()
