"""Native Postgres checkpoint-history regressions.

These fixtures deliberately use ``AsyncPostgresSaver`` rather than a mocked
checkpointer: historical projections must retain native blob, write, namespace,
and parent-config semantics while avoiding unrelated historical channel blobs.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import ormsgpack
import pytest
from deepagents.graph import DeepAgentState
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.graph import END, START, StateGraph

from src.progena.persistence.langgraph import _open_saver
from src.progena.sessions.checkpoint_history import read_message_checkpoint
from src.progena.sessions.history import load_conversation, load_conversation_messages

_THREAD_ID = "native-history-projection"
_ROOT_BASE = "root-base"
_ROOT_MID = "root-mid"
_ROOT_SELECTED = "root-selected"
_ROOT_FORK = "root-fork"
_CHILD_START = "child-start"
_CHILD_SUMMARY = "child-summary"
_CHILD_LIVE = "child-live"
_NESTED_SUMMARY = "nested-summary"
_CHILD_NS = "tools:task-parent"
_NESTED_NS = "tools:task-parent|tools:task-nested"

_DELTA_THREAD_ID = "native-delta-history-projection"


async def _reply_from_delta_state(state: DeepAgentState) -> dict[str, list[AIMessage]]:
    request = state["messages"][-1]
    return {
        "messages": [
            AIMessage(
                content=f"answer to {request.content}",
                id=f"{request.id}-answer",
                additional_kwargs={"nested": {"source_message_id": request.id}},
            )
        ]
    }


async def _store_blob_channel(
    saver: Any,
    thread_id: str,
    checkpoint_id: str,
    channel: str,
    type_tag: str,
    blob: bytes,
) -> None:
    version = f"{checkpoint_id}:{channel}:projection"
    async with saver._cursor() as cursor:
        await cursor.execute(
            """
            UPDATE checkpoints
            SET checkpoint = jsonb_set(
                checkpoint,
                ARRAY['channel_versions', %s],
                to_jsonb(%s::text),
                true
            )
            WHERE thread_id = %s
              AND checkpoint_ns = ''
              AND checkpoint_id = %s
            """,
            (channel, version, thread_id, checkpoint_id),
        )
        await cursor.execute(
            """
            INSERT INTO checkpoint_blobs
                (thread_id, checkpoint_ns, channel, version, type, blob)
            VALUES (%s, '', %s, %s, %s, %s)
            ON CONFLICT (thread_id, checkpoint_ns, channel, version)
            DO UPDATE SET type = EXCLUDED.type, blob = EXCLUDED.blob
            """,
            (thread_id, channel, version, type_tag, blob),
        )


async def _store_summary_event(
    saver: Any,
    thread_id: str,
    checkpoint_id: str,
    summary: str,
    created_at_index: int,
) -> None:
    type_tag, blob = saver.serde.dumps_typed(_event(summary, created_at_index))
    await _store_blob_channel(
        saver,
        thread_id,
        checkpoint_id,
        "_summarization_event",
        type_tag,
        blob,
    )


async def _seed_delta_turns(saver: Any) -> tuple[str, str]:
    """Write two real DeepAgentState turns, retaining DeltaChannel snapshots."""
    graph = StateGraph(DeepAgentState)
    graph.add_node("reply", _reply_from_delta_state)
    graph.add_edge(START, "reply")
    graph.add_edge("reply", END)
    app = graph.compile(checkpointer=saver)
    config = {"configurable": {"thread_id": _DELTA_THREAD_ID}}

    await app.ainvoke(
        {
            "messages": [
                HumanMessage(
                    content="first nested request",
                    id="delta-user-one",
                    additional_kwargs={"nested": {"turn": 1, "parts": ["one"]}},
                )
            ]
        },
        config,
    )
    first_tip = await saver.aget_tuple(config)
    assert first_tip is not None
    first_checkpoint_id = str(first_tip.config["configurable"]["checkpoint_id"])

    await app.ainvoke(
        {
            "messages": [
                HumanMessage(
                    content="second nested request",
                    id="delta-user-two",
                    additional_kwargs={"nested": {"turn": 2, "parts": ["two"]}},
                )
            ]
        },
        config,
    )
    latest_tip = await saver.aget_tuple(config)
    assert latest_tip is not None
    latest_checkpoint_id = str(latest_tip.config["configurable"]["checkpoint_id"])

    await _store_summary_event(
        saver, _DELTA_THREAD_ID, first_checkpoint_id, "first native summary", 2
    )
    await _store_summary_event(
        saver, _DELTA_THREAD_ID, latest_checkpoint_id, "latest native summary", 4
    )
    # The selected tip itself—not just an unreachable ancestor—now contains
    # an unreadable unrelated blob. A full native tuple load must fail.
    await _store_blob_channel(
        saver,
        _DELTA_THREAD_ID,
        latest_checkpoint_id,
        "files",
        "msgpack",
        b"\xc1",
    )
    return first_checkpoint_id, latest_checkpoint_id


@pytest.fixture
async def checkpointer(postgres_url: str) -> Any:
    saver, connection = await _open_saver(postgres_url)
    try:
        yield saver
    finally:
        await connection.close()


def _timestamp(second: int) -> str:
    return datetime(2026, 10, 30, 12, 0, second, tzinfo=UTC).isoformat()


def _event(summary: str, created_at_index: int) -> dict[str, Any]:
    return {
        "summary_message": {"content": summary},
        "cutoff_index": created_at_index - 1,
        "created_at_index": created_at_index,
        "file_path": "/native/private-summary",
    }


async def _put(
    saver: Any,
    *,
    checkpoint_id: str,
    timestamp: str,
    namespace: str = "",
    parent_checkpoint_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    messages: list[Any] | None = None,
    summary: dict[str, Any] | None = None,
    files: Any = None,
) -> dict[str, Any]:
    """Store one real native checkpoint, including selected blob channel versions."""
    values: dict[str, Any] = {}
    if messages is not None:
        values["messages"] = messages
    if summary is not None:
        values["_summarization_event"] = summary
    if files is not None:
        values["files"] = files

    checkpoint = empty_checkpoint()
    checkpoint.update(
        {
            "id": checkpoint_id,
            "ts": timestamp,
            "channel_values": values,
            "channel_versions": {channel: f"{checkpoint_id}:{channel}" for channel in values},
            "versions_seen": {},
        }
    )
    configurable: dict[str, str] = {
        "thread_id": _THREAD_ID,
        "checkpoint_ns": namespace,
    }
    if parent_checkpoint_id is not None:
        configurable["checkpoint_id"] = parent_checkpoint_id
    return await saver.aput(
        {"configurable": configurable},
        checkpoint,
        metadata or {},
        checkpoint["channel_versions"],
    )


async def _writes(saver: Any, config: dict[str, Any], task_id: str, *messages: Any) -> None:
    await saver.aput_writes(
        config,
        [("messages", message) for message in messages],
        task_id=task_id,
    )


async def _seed_lineages(saver: Any) -> None:
    """Create a selected root branch, a fork, and independently advancing children."""
    root_user = HumanMessage(content="original request", id="root-user")
    root_agent = AIMessage(content="original response", id="root-agent")
    root_base = await _put(
        saver,
        checkpoint_id=_ROOT_BASE,
        timestamp=_timestamp(1),
        messages=[root_user],
        files={"unrelated": "old file state"},
    )
    await _writes(saver, root_base, "input", root_user)

    root_mid = await _put(
        saver,
        checkpoint_id=_ROOT_MID,
        timestamp=_timestamp(6),
        parent_checkpoint_id=_ROOT_BASE,
        messages=[root_user],
    )
    await _writes(saver, root_mid, "respond", root_agent)
    # A pending compaction write must not become a rendered historical marker
    # until it appears in a checkpoint's committed channel values.
    await saver.aput_writes(
        root_mid,
        [("_summarization_event", _event("pending summary must not render", 1))],
        task_id="summarize",
    )

    root_selected = await _put(
        saver,
        checkpoint_id=_ROOT_SELECTED,
        timestamp=_timestamp(10),
        parent_checkpoint_id=_ROOT_MID,
        messages=[root_user, root_agent],
        summary=_event("root summary", 2),
    )
    await _writes(
        saver,
        root_selected,
        "task-parent",
        ToolMessage(content="", tool_call_id="call-parent", id="root-task-message"),
    )

    await _put(
        saver,
        checkpoint_id=_ROOT_FORK,
        timestamp=_timestamp(12),
        parent_checkpoint_id=_ROOT_MID,
        messages=[root_user, AIMessage(content="fork response", id="fork-agent")],
        summary=_event("fork summary must not render", 2),
    )

    child_start = await _put(
        saver,
        checkpoint_id=_CHILD_START,
        timestamp=_timestamp(3),
        namespace=_CHILD_NS,
        metadata={"parents": {"": _ROOT_SELECTED}, "lc_agent_name": "researcher"},
        messages=[HumanMessage(content="delegate", id="child-request")],
    )
    await _writes(
        saver,
        child_start,
        "task-nested",
        ToolMessage(content="", tool_call_id="call-nested", id="child-task-message"),
    )

    await _put(
        saver,
        checkpoint_id=_CHILD_SUMMARY,
        timestamp=_timestamp(5),
        namespace=_CHILD_NS,
        parent_checkpoint_id=_CHILD_START,
        metadata={"parents": {"": _ROOT_SELECTED}, "lc_agent_name": "researcher"},
        messages=[
            HumanMessage(content="delegate", id="child-request"),
            AIMessage(content="researched", id="child-result"),
        ],
        summary=_event("child summary", 1),
    )

    await _put(
        saver,
        checkpoint_id=_CHILD_LIVE,
        timestamp=_timestamp(11),
        namespace=_CHILD_NS,
        parent_checkpoint_id=_CHILD_SUMMARY,
        metadata={"parents": {"": _ROOT_SELECTED}, "lc_agent_name": "researcher"},
        messages=[
            HumanMessage(content="delegate", id="child-request"),
            AIMessage(content="live trace advanced", id="child-live-result"),
        ],
        summary=_event("live child summary must not render", 2),
    )

    await _put(
        saver,
        checkpoint_id=_NESTED_SUMMARY,
        timestamp=_timestamp(4),
        namespace=_NESTED_NS,
        metadata={
            "parents": {_CHILD_NS: _CHILD_START},
            "lc_agent_name": "critic",
        },
        messages=[HumanMessage(content="critique", id="nested-request")],
        summary=_event("nested summary", 1),
    )

    # The selected tip has its own messages snapshot, so history must not touch
    # this prior, unrelated channel blob while assembling metadata.
    async with saver._cursor() as cursor:
        await cursor.execute(
            """
            UPDATE checkpoint_blobs
            SET blob = %s
            WHERE thread_id = %s
              AND checkpoint_ns = ''
              AND channel = 'files'
              AND version = %s
            """,
            (b"not-a-decodable-file-blob", _THREAD_ID, f"{_ROOT_BASE}:files"),
        )


async def test_load_conversation_projects_only_selected_native_summary_lineage(
    checkpointer: Any,
) -> None:
    await _seed_lineages(checkpointer)

    conversation = await load_conversation(
        checkpointer,
        _THREAD_ID,
        checkpoint_id=_ROOT_SELECTED,
        include_summaries=True,
    )

    assert conversation is not None
    assert conversation.checkpoint_id == _ROOT_SELECTED
    assert [(message.id, message.content) for message in conversation.root.messages] == [
        ("root-user", "original request"),
        ("root-agent", "original response"),
    ]
    assert conversation.root.committed_at == {
        "root-user": datetime(2026, 10, 30, 12, 0, 6, tzinfo=UTC),
        "root-agent": datetime(2026, 10, 30, 12, 0, 10, tzinfo=UTC),
        "root-task-message": datetime(2026, 10, 30, 12, 0, 10, tzinfo=UTC),
    }

    assert {
        tool_call_id: [(message.id, message.content) for message in thread.messages]
        for tool_call_id, thread in conversation.subagents.items()
    } == {
        "call-parent": [
            ("child-request", "delegate"),
            ("child-result", "researched"),
        ],
        "call-nested": [("nested-request", "critique")],
    }
    assert [
        (marker.id, marker.anchor_message_id, marker.scope) for marker in conversation.summaries
    ] == [
        (
            f"summary:{_NESTED_NS}:{_NESTED_SUMMARY}",
            "nested-request",
            {"kind": "subagent", "id": "call-nested", "label": "critic"},
        ),
        (
            f"summary:{_CHILD_NS}:{_CHILD_SUMMARY}",
            "child-request",
            {"kind": "subagent", "id": "call-parent", "label": "researcher"},
        ),
        (
            f"summary:root:{_ROOT_SELECTED}",
            "root-agent",
            {"kind": "conversation", "id": _THREAD_ID},
        ),
    ]
    assert [marker.summary for marker in conversation.summaries] == [
        "nested summary",
        "child summary",
        "root summary",
    ]


async def test_load_conversation_keeps_live_child_trace_independent_of_summary_bounds(
    checkpointer: Any,
) -> None:
    await _seed_lineages(checkpointer)

    conversation = await load_conversation(
        checkpointer,
        _THREAD_ID,
        checkpoint_id=_ROOT_SELECTED,
        include_summaries=False,
    )

    assert conversation is not None
    assert conversation.summaries == ()
    assert [(message.id, message.content) for message in conversation.root.messages] == [
        ("root-user", "original request"),
        ("root-agent", "original response"),
    ]
    assert [
        (message.id, message.content) for message in conversation.subagents["call-parent"].messages
    ] == [
        ("child-request", "delegate"),
        ("child-live-result", "live trace advanced"),
    ]


async def test_message_projection_reads_real_delta_turns_despite_poisoned_selected_files(
    checkpointer: Any,
) -> None:
    first_checkpoint_id, latest_checkpoint_id = await _seed_delta_turns(checkpointer)
    latest_config = {
        "configurable": {
            "thread_id": _DELTA_THREAD_ID,
            "checkpoint_id": latest_checkpoint_id,
        }
    }

    # This is intentionally the normal saver path that the projection replaces:
    # it joins every channel blob and reaches the poisoned selected-tip file.
    with pytest.raises(ormsgpack.MsgpackDecodeError):
        await checkpointer.aget_tuple(latest_config)

    projected_tip = await read_message_checkpoint(checkpointer, latest_config)
    assert projected_tip is not None
    assert projected_tip.config["configurable"]["checkpoint_id"] == latest_checkpoint_id

    latest_messages = await load_conversation_messages(
        checkpointer,
        _DELTA_THREAD_ID,
        checkpoint_id=latest_checkpoint_id,
    )
    first_messages = await load_conversation_messages(
        checkpointer,
        _DELTA_THREAD_ID,
        checkpoint_id=first_checkpoint_id,
    )

    assert latest_messages is not None
    assert [
        (message.id, message.content, message.additional_kwargs) for message in latest_messages
    ] == [
        (
            "delta-user-one",
            "first nested request",
            {"nested": {"turn": 1, "parts": ["one"]}},
        ),
        (
            "delta-user-one-answer",
            "answer to first nested request",
            {"nested": {"source_message_id": "delta-user-one"}},
        ),
        (
            "delta-user-two",
            "second nested request",
            {"nested": {"turn": 2, "parts": ["two"]}},
        ),
        (
            "delta-user-two-answer",
            "answer to second nested request",
            {"nested": {"source_message_id": "delta-user-two"}},
        ),
    ]
    assert first_messages is not None
    assert [(message.id, message.content) for message in first_messages] == [
        ("delta-user-one", "first nested request"),
        ("delta-user-one-answer", "answer to first nested request"),
    ]

    conversation = await load_conversation(
        checkpointer,
        _DELTA_THREAD_ID,
        checkpoint_id=latest_checkpoint_id,
        include_summaries=True,
        include_subagents=False,
    )

    assert conversation is not None
    assert [(message.id, message.content) for message in conversation.root.messages] == [
        ("delta-user-one", "first nested request"),
        ("delta-user-one-answer", "answer to first nested request"),
        ("delta-user-two", "second nested request"),
        ("delta-user-two-answer", "answer to second nested request"),
    ]
    assert [marker.summary for marker in conversation.summaries] == [
        "first native summary",
        "latest native summary",
    ]
    assert (
        await read_message_checkpoint(
            checkpointer,
            {"configurable": {"thread_id": "missing-native-history-thread"}},
        )
        is None
    )
    assert (
        await load_conversation_messages(
            checkpointer,
            "missing-native-history-thread",
        )
        is None
    )
