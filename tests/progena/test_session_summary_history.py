from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.messages import ToolMessage

import src.progena.sessions.history as history_module
from src.progena.sessions.history import _summary_occurrences
from src.progena.sessions.transcript import ThreadMessages


def _checkpoint(
    checkpoint_id: str,
    *,
    event: dict | None = None,
    pending_event: dict | None = None,
) -> SimpleNamespace:
    values = {} if event is None else {"_summarization_event": event}
    pending_writes = ()
    if pending_event is not None:
        pending_writes = (("task", "_summarization_event", pending_event),)
    return SimpleNamespace(
        config={"configurable": {"checkpoint_id": checkpoint_id}},
        checkpoint={
            "ts": f"2026-10-30T00:00:0{len(checkpoint_id)}Z",
            "channel_values": values,
        },
        pending_writes=pending_writes,
    )


def _event(summary: str, *, cutoff_index: int, created_at_index: int) -> dict:
    return {
        "summary_message": {"content": f"<summary>\n{summary}\n</summary>"},
        "cutoff_index": cutoff_index,
        "created_at_index": created_at_index,
        "file_path": "/private/native-detail",
    }


def test_summary_history_uses_committed_channel_values_and_recovers_overwrites() -> None:
    first = _event("first native summary", cutoff_index=1, created_at_index=3)
    second = _event("second native summary", cutoff_index=4, created_at_index=6)

    occurrences = _summary_occurrences(
        [
            _checkpoint("p", pending_event=first),
            _checkpoint("a", event=first),
            _checkpoint("b", event=first),
            _checkpoint("c", event=second),
        ],
        ns="",
    )

    assert [(item.id, item.summary, item.created_at_index) for item in occurrences] == [
        ("summary:root:a", "first native summary", 3),
        ("summary:root:c", "second native summary", 6),
    ]


def _linked_checkpoint(
    checkpoint_id: str,
    *,
    ns: str,
    timestamp: str,
    parent_ns: str,
    parent_id: str,
    pending_writes: tuple[Any, ...] = (),
) -> SimpleNamespace:
    return SimpleNamespace(
        config={
            "configurable": {
                "thread_id": "thread-1",
                "checkpoint_ns": ns,
                "checkpoint_id": checkpoint_id,
            }
        },
        checkpoint={"ts": timestamp, "channel_values": {}},
        metadata={"parents": {parent_ns: parent_id}},
        pending_writes=pending_writes,
        parent_config=None,
    )


def _loaded(checkpoint: SimpleNamespace) -> history_module._LoadedThread:
    timestamp = datetime.fromisoformat(checkpoint.checkpoint["ts"].replace("Z", "+00:00"))
    return history_module._LoadedThread(
        thread=ThreadMessages(messages=[], committed_at={}),
        checkpoint_id=checkpoint.config["configurable"]["checkpoint_id"],
        summaries=(),
        checkpoint_ids=frozenset({checkpoint.config["configurable"]["checkpoint_id"]}),
        checkpoint_time=timestamp.astimezone(UTC),
        history=(checkpoint,),
    )


async def test_subagent_summaries_use_only_the_selected_root_lineage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root_checkpoint = _linked_checkpoint(
        "root-old",
        ns="",
        timestamp="2026-10-30T00:00:10Z",
        parent_ns="",
        parent_id="",
        pending_writes=(("task-a", "messages", ToolMessage(content="", tool_call_id="call-a")),),
    )
    child = _linked_checkpoint(
        "child-old",
        ns="tools:task-a",
        timestamp="2026-10-30T00:00:09Z",
        parent_ns="",
        parent_id="root-old",
        pending_writes=(("task-b", "messages", ToolMessage(content="", tool_call_id="call-b")),),
    )
    future_child = _linked_checkpoint(
        "child-future",
        ns="tools:task-a",
        timestamp="2026-10-30T00:00:11Z",
        parent_ns="",
        parent_id="root-old",
    )
    fork_child = _linked_checkpoint(
        "child-fork",
        ns="tools:task-a",
        timestamp="2026-10-30T00:00:09Z",
        parent_ns="",
        parent_id="root-new",
    )
    nested = _linked_checkpoint(
        "nested-old",
        ns="tools:task-a|tools:task-b",
        timestamp="2026-10-30T00:00:08Z",
        parent_ns="tools:task-a",
        parent_id="child-old",
    )
    future_nested = _linked_checkpoint(
        "nested-future",
        ns="tools:task-a|tools:task-b",
        timestamp="2026-10-30T00:00:10Z",
        parent_ns="tools:task-a",
        parent_id="child-old",
    )

    selected: list[tuple[str, str]] = []

    async def _load_selected(
        _checkpointer: Any,
        _thread_id: str,
        ns: str,
        *,
        history: Any,
        checkpoint_id: str | None = None,
        include_summaries: bool = False,
    ) -> history_module._LoadedThread:
        assert checkpoint_id is not None
        selected.append((ns, checkpoint_id))
        checkpoints = {
            "child-old": child,
            "nested-old": nested,
        }
        return _loaded(checkpoints[checkpoint_id])

    monkeypatch.setattr(history_module, "_load_thread", _load_selected)
    subagents = await history_module._selected_subagent_threads(
        object(),
        "thread-1",
        _loaded(root_checkpoint),
        include_summaries=True,
        history={
            namespace: {
                checkpoint.config["configurable"]["checkpoint_id"]: checkpoint
                for checkpoint in (future_child, fork_child, child, future_nested, nested)
                if checkpoint.config["configurable"]["checkpoint_ns"] == namespace
            }
            for namespace in ("tools:task-a", "tools:task-a|tools:task-b")
        },
    )

    assert selected == [
        ("tools:task-a", "child-old"),
        ("tools:task-a|tools:task-b", "nested-old"),
    ]
    assert set(subagents) == {"call-a", "call-b"}
