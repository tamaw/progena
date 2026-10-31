"""Behavioral regression tests for durable queued chat steering."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from src.progena.agent.middleware.queue_steering import QueueSteeringMiddleware
from src.progena.persistence.langgraph import _open_store
from src.progena.persistence.store import LangGraphRuntimeStore
from src.progena.runtime.chat_queue import ChatQueueStore, QueueActionError
from src.progena.runtime.streaming.stream_events import normalize_custom_event

pytestmark = pytest.mark.asyncio


class _ToolFakeModel(GenericFakeChatModel):
    """Generic fake model whose tool binding leaves scripted messages intact."""

    def bind_tools(self, tools: Any, **kwargs: Any) -> _ToolFakeModel:
        del tools, kwargs
        return self


def _queue() -> ChatQueueStore:
    return ChatQueueStore(LangGraphRuntimeStore(InMemoryStore()))


async def _enqueue(queue: ChatQueueStore, conversation_id: str, response_id: str) -> str:
    _snapshot, message_id = await queue.enqueue(
        conversation_id,
        text=response_id,
        attachments=[{"id": f"att-{response_id}"}],
        metadata={"request_id": response_id, "source": "ui"},
        source="ui",
        received_at=1.0,
        response_id=response_id,
        model="test",
    )
    return message_id


async def test_queue_control_event_preserves_long_authoritative_metadata() -> None:
    text = "queued prompt " * 400
    attachments = [{"id": "attachment-1", "name": "source.pdf"}]
    event = normalize_custom_event(
        {
            "event_type": "chat_queue_boundary",
            "content": "",
            "metadata": {
                "message_id": "user_response",
                "request_id": "response",
                "text": text,
                "attachments": attachments,
            },
        }
    )
    assert event == {
        "event_type": "chat_queue_boundary",
        "content": "",
        "metadata": {
            "message_id": "user_response",
            "request_id": "response",
            "text": text,
            "attachments": attachments,
        },
    }


async def test_claim_recovery_uses_checkpoint_message_ids() -> None:
    queue = _queue()
    message_id = await _enqueue(queue, "c1", "r1")

    claimed = await queue.claim_next("c1")
    assert claimed is not None and claimed.id == message_id
    assert (await queue.snapshot("c1"))["messages"][0]["status"] == "sending"

    restored = await queue.recover("c1", set())
    assert restored["messages"][0]["status"] == "queued"

    claimed = await queue.claim_next("c1")
    assert claimed is not None and claimed.id == message_id
    recovered = await queue.recover("c1", {message_id})
    assert recovered["messages"] == []


async def test_actions_are_idempotent_revisioned_and_do_not_mutate_claims() -> None:
    queue = _queue()
    first = await _enqueue(queue, "c1", "r1")
    second = await _enqueue(queue, "c1", "r2")
    initial = await queue.snapshot("c1")

    result = await queue.action(
        "c1",
        {
            "action": "send_now",
            "message_id": second,
            "revision": initial["revision"],
            "command_id": "cmd-1",
        },
    )
    assert [item["id"] for item in result["messages"]] == [first, second]
    assert result["messages"][1]["send_now"] is True
    assert (
        await queue.action(
            "c1",
            {
                "action": "send_now",
                "message_id": second,
                "revision": initial["revision"],
                "command_id": "cmd-1",
            },
        )
    ) == result

    claimed = await queue.claim_next("c1")
    assert claimed is not None and claimed.send_now is True
    claimed_snapshot = await queue.snapshot("c1")
    with pytest.raises(QueueActionError) as error:
        await queue.action(
            "c1",
            {
                "action": "delete",
                "message_id": second,
                "revision": claimed_snapshot["revision"],
                "command_id": "cmd-2",
            },
        )
    assert error.value.code == "queue_message_claimed"


async def test_concurrent_enqueues_keep_each_input_once() -> None:
    queue = _queue()
    ids = await asyncio.gather(*(_enqueue(queue, "c1", f"r{index}") for index in range(12)))
    snapshot = await queue.snapshot("c1")
    assert snapshot["revision"] == 12
    assert {item["id"] for item in snapshot["messages"]} == set(ids)


async def test_native_postgres_claim_handles_mapping_rows(postgres_url: str) -> None:
    native_store, pool = await _open_store(postgres_url)
    queue = ChatQueueStore(LangGraphRuntimeStore(native_store))
    try:
        message_id = await _enqueue(queue, "native-conversation", "native-response")
        claimed = await queue.claim_next("native-conversation")
        assert claimed is not None
        assert claimed.id == message_id
    finally:
        await pool.close()


async def test_native_postgres_first_enqueues_do_not_replace_each_other(postgres_url: str) -> None:
    native_store, pool = await _open_store(postgres_url)
    queue = ChatQueueStore(LangGraphRuntimeStore(native_store))
    try:
        first, second = await asyncio.gather(
            _enqueue(queue, "first-write-race", "one"),
            _enqueue(queue, "first-write-race", "two"),
        )
        snapshot = await queue.snapshot("first-write-race")
        assert {item["id"] for item in snapshot["messages"]} == {first, second}
    finally:
        await pool.close()


async def test_fake_model_only_steers_selected_input_after_blocked_tool_results() -> None:
    queue = _queue()
    middleware = QueueSteeringMiddleware(queue)
    tool_started = asyncio.Event()
    release_tool = asyncio.Event()

    @tool
    async def wait_for_release() -> str:
        """Wait until the test releases the tool result."""
        tool_started.set()
        await release_tool.wait()
        return "done"

    model = _ToolFakeModel(
        messages=iter(
            [
                AIMessage(
                    content="",
                    tool_calls=[{"name": "wait_for_release", "args": {}, "id": "call-1"}],
                ),
                AIMessage(content="steered reply"),
            ]
        )
    )
    graph = create_agent(
        model=model,
        tools=[wait_for_release],
        middleware=[middleware],
        checkpointer=InMemorySaver(),
    )
    config = {"configurable": {"thread_id": "c1"}}
    invoke = asyncio.create_task(
        graph.ainvoke({"messages": [HumanMessage(id="msg-initial", content="initial")]}, config)
    )
    await tool_started.wait()
    normal_id = await _enqueue(queue, "c1", "r-normal")
    queued_id = await _enqueue(queue, "c1", "r-steer")
    snapshot = await queue.snapshot("c1")
    await queue.action(
        "c1",
        {
            "action": "send_now",
            "message_id": queued_id,
            "revision": snapshot["revision"],
            "command_id": "send-now",
        },
    )
    release_tool.set()
    result = await invoke

    messages = result["messages"]
    tool_index = next(
        index for index, message in enumerate(messages) if isinstance(message, ToolMessage)
    )
    queued_index = next(index for index, message in enumerate(messages) if message.id == queued_id)
    assert queued_index == tool_index + 1
    assert isinstance(messages[queued_index], HumanMessage)
    assert messages[queued_index].additional_kwargs["attachments"] == [{"id": "att-r-steer"}]
    assert messages[queued_index].additional_kwargs["progena_queue_send_now"] is True

    await queue.recover("c1", {message.id for message in messages if isinstance(message.id, str)})
    remaining = await queue.snapshot("c1")
    assert [item["id"] for item in remaining["messages"]] == [normal_id]
