"""Behavioral regression tests for durable queued chat steering."""

from __future__ import annotations

import asyncio

import pytest
from langgraph.store.memory import InMemoryStore

from src.progena.persistence.langgraph import _open_store
from src.progena.persistence.store import LangGraphRuntimeStore
from src.progena.runtime.chat_queue import ChatQueueStore, QueueActionError

pytestmark = pytest.mark.asyncio


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


async def test_recovery_retains_checkpointed_claim_until_terminal_settlement() -> None:
    queue = _queue()
    message_id = await _enqueue(queue, "c1", "r1")

    claimed = await queue.claim_next("c1")
    assert claimed is not None and claimed.id == message_id
    assert (await queue.snapshot("c1"))["messages"][0]["status"] == "sending"

    restored = await queue.recover("c1", set())
    assert restored["messages"][0]["status"] == "queued"

    claimed = await queue.claim_next("c1")
    assert claimed is not None and claimed.id == message_id
    pending = await queue.recover("c1", {message_id})
    assert pending["messages"][0]["status"] == "sending"
    resumed = await queue.claim_recoverable("c1", {message_id})
    assert resumed == claimed

    recovered = await queue.recover("c1", {message_id}, terminal_message_ids={message_id})
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
    repeated, applied = await queue.action(
        "c1",
        {
            "action": "send_now",
            "message_id": second,
            "revision": initial["revision"],
            "command_id": "cmd-1",
        },
        include_applied=True,
    )
    assert repeated == result
    assert applied is False

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


async def test_send_now_durably_cancels_the_sending_claim_without_resuming_it() -> None:
    queue = _queue()
    running = await _enqueue(queue, "c1", "running")
    selected = await _enqueue(queue, "c1", "selected")
    claimed = await queue.claim_next("c1")
    assert claimed is not None and claimed.id == running

    snapshot = await queue.snapshot("c1")
    _updated, applied = await queue.action(
        "c1",
        {
            "action": "send_now",
            "message_id": selected,
            "revision": snapshot["revision"],
            "command_id": "send-selected-now",
        },
        include_applied=True,
    )
    assert applied is True
    assert [claim.id for claim in await queue.cancel_requested_claims("c1")] == [running]
    assert await queue.claim_recoverable("c1", {running}) is None

    recovered = await queue.recover("c1", {running})
    assert recovered["messages"][0]["status"] == "sending"
    await queue.settle_claim("c1", running)
    next_claim = await queue.claim_next("c1")
    assert next_claim is not None and next_claim.id == selected and next_claim.send_now


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


async def test_native_postgres_concurrent_claims_reserve_one_message(postgres_url: str) -> None:
    native_store, pool = await _open_store(postgres_url)
    queue = ChatQueueStore(LangGraphRuntimeStore(native_store))
    try:
        message_id = await _enqueue(queue, "claim-race", "one")
        first, second = await asyncio.gather(
            queue.claim_next("claim-race"), queue.claim_next("claim-race")
        )
        claims = [claim for claim in (first, second) if claim is not None]
        assert [claim.id for claim in claims] == [message_id]
        assert (await queue.snapshot("claim-race"))["messages"][0]["status"] == "sending"
    finally:
        await pool.close()
