"""Load a chat's messages from the LangGraph checkpointer for rendering.

Messages are rebuilt the way ``aget_state`` does (``achannels_from_checkpoint``),
which also covers the main agent's delta-encoded ``messages`` channel, where
the latest checkpoint may hold no value or a ``_DeltaSnapshot``. It runs per
checkpoint namespace because subagents started by the ``task`` tool checkpoint
under their own ``tools:<task_id>`` namespace, which ``aget_state`` only exposes
while that subagent is mid-run.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from deepagents.graph import DeepAgentState
from langchain_core.messages import ToolMessage
from langgraph.checkpoint.base import CheckpointTuple
from langgraph.graph import StateGraph
from langgraph.pregel._checkpoint import achannels_from_checkpoint

from src.progena.sessions.checkpoint_history import (
    NATIVE_SUMMARIZATION_EVENT_KEY,
    CheckpointHistory,
    read_checkpoint_history,
    read_message_checkpoint,
)
from src.progena.utils import normalize_utc_datetime

from .transcript import DEFAULT_AGENT_NICK, ThreadMessages

# The main agent's ``messages`` channel spec: a DeltaChannel. Subagent
# namespaces store plain lists, which the same spec restores as-is.
_MESSAGES_SPECS = {"messages": StateGraph(DeepAgentState).channels["messages"]}


@dataclass(frozen=True)
class SummaryOccurrence:
    """One native summary state update that reached a committed checkpoint."""

    id: str
    summary: str
    created_at_index: int
    committed_at: datetime | None


@dataclass(frozen=True)
class ConversationSummary:
    """A committed summary plus the thread scope that owns its boundary."""

    id: str
    summary: str
    created_at_index: int
    anchor_message_id: str | None
    scope: dict[str, str]
    committed_at: datetime | None


@dataclass(frozen=True)
class _LoadedThread:
    thread: ThreadMessages
    checkpoint_id: str
    summaries: tuple[SummaryOccurrence, ...]
    checkpoint_ids: frozenset[str]
    checkpoint_time: datetime | None
    history: tuple[CheckpointTuple, ...]


@dataclass(frozen=True)
class Conversation:
    """A chat's root thread, the subagent threads it delegated to, and its newest checkpoint."""

    root: ThreadMessages
    subagents: dict[str, ThreadMessages]
    checkpoint_id: str
    summaries: tuple[ConversationSummary, ...] = ()


async def latest_checkpoint_id(checkpointer: Any, thread_id: str) -> str | None:
    """The chat's newest root checkpoint id, or ``None`` if it has never run."""
    tip = await checkpointer.aget_tuple(_config(thread_id, ""))
    return str(tip.config["configurable"]["checkpoint_id"]) if tip is not None else None


async def load_conversation_messages(
    checkpointer: Any, thread_id: str, *, checkpoint_id: str | None = None
) -> list[Any] | None:
    """Read native snapshot messages, including null-task input updates."""
    from langgraph._internal._constants import NULL_TASK_ID

    tip = await read_message_checkpoint(checkpointer, _config(thread_id, "", checkpoint_id))
    if tip is None:
        return None
    channels, _ = await achannels_from_checkpoint(
        _MESSAGES_SPECS, tip.checkpoint, saver=checkpointer, config=tip.config
    )
    updates = [
        value
        for task_id, channel, value in tip.pending_writes or ()
        if task_id == NULL_TASK_ID and channel == "messages"
    ]
    if updates:
        channels["messages"].update(updates)
    return list(channels["messages"].get()) if channels["messages"].is_available() else []


async def _checkpoint_messages(checkpointer: Any, tip: CheckpointTuple) -> list[Any]:
    channels, _ = await achannels_from_checkpoint(
        _MESSAGES_SPECS, tip.checkpoint, saver=checkpointer, config=tip.config
    )
    return list(channels["messages"].get()) if channels["messages"].is_available() else []


async def load_conversation(
    checkpointer: Any,
    thread_id: str,
    *,
    checkpoint_id: str | None = None,
    include_summaries: bool = False,
    include_subagents: bool = True,
) -> Conversation | None:
    """Load the chat's committed messages, and optionally native summaries."""
    root_tip = await read_message_checkpoint(checkpointer, _config(thread_id, "", checkpoint_id))
    if root_tip is None:
        return None
    history = await read_checkpoint_history(
        checkpointer, thread_id, include_summaries=include_summaries
    )
    root = await _load_thread(
        checkpointer,
        thread_id,
        "",
        checkpoint_id=checkpoint_id,
        include_summaries=include_summaries,
        history=history,
        tip=root_tip,
    )
    if root is None:
        return None
    subagents: dict[str, ThreadMessages] = {}
    summaries: list[ConversationSummary] = []
    if include_summaries:
        for occurrence in root.summaries:
            marker = _conversation_summary(
                occurrence,
                root.thread.messages,
                {"kind": "conversation", "id": thread_id},
            )
            if marker is not None:
                summaries.append(marker)
    if include_subagents:
        for tool_call_id, loaded in (
            await _selected_subagent_threads(
                checkpointer, thread_id, root, include_summaries, history=history
            )
        ).items():
            subagents[tool_call_id] = loaded.thread
            if not include_summaries:
                continue
            scope = {"kind": "subagent", "id": tool_call_id}
            if loaded.thread.nick:
                scope["label"] = loaded.thread.nick
            for occurrence in loaded.summaries:
                marker = _conversation_summary(occurrence, loaded.thread.messages, scope)
                if marker is not None:
                    summaries.append(marker)
    summaries.sort(
        key=lambda summary: (
            summary.committed_at is None,
            summary.committed_at.isoformat() if summary.committed_at is not None else "",
        )
    )
    return Conversation(
        root=root.thread,
        subagents=subagents,
        checkpoint_id=root.checkpoint_id,
        summaries=tuple(summaries),
    )


async def _load_thread(
    checkpointer: Any,
    thread_id: str,
    ns: str,
    *,
    history: CheckpointHistory,
    checkpoint_id: str | None = None,
    include_summaries: bool = False,
    tip: CheckpointTuple | None = None,
) -> _LoadedThread | None:
    if tip is None:
        tip = await read_message_checkpoint(checkpointer, _config(thread_id, ns, checkpoint_id))
    if tip is None:
        return None
    ancestry = _checkpoint_ancestry(thread_id, ns, tip, history.get(ns, {}))
    chronological_history = tuple(reversed(ancestry))
    messages = await _checkpoint_messages(checkpointer, tip)
    thread = ThreadMessages(
        messages=messages,
        committed_at=_commit_times(list(chronological_history)),
        # An unnamed subagent namespace gets "" so the renderer uses the task's subagent_type.
        nick=str(tip.metadata.get("lc_agent_name") or ("" if ns else DEFAULT_AGENT_NICK)),
    )
    return _LoadedThread(
        thread=thread,
        checkpoint_id=_checkpoint_id(tip),
        summaries=(
            tuple(_summary_occurrences(list(chronological_history), ns=ns))
            if include_summaries
            else ()
        ),
        checkpoint_ids=frozenset(
            _checkpoint_id(checkpoint) for checkpoint in chronological_history
        ),
        checkpoint_time=_checkpoint_time(tip),
        history=chronological_history,
    )


def _checkpoint_ancestry(
    thread_id: str,
    ns: str,
    tip: CheckpointTuple,
    checkpoints: Mapping[str, CheckpointTuple],
) -> list[CheckpointTuple]:
    """Return one checkpoint's local ancestor chain, newest first.

    Listing a namespace also returns forked checkpoints.  Following native
    ``parent_config`` instead keeps a historical render on the requested
    checkpoint's actual ancestry.
    """
    # The request-local index contains only history metadata, not every
    # checkpoint's unrelated channel values.
    tip = checkpoints.get(_checkpoint_id(tip), tip)
    history = [tip]
    seen = {_checkpoint_id(tip)}
    current = tip
    while True:
        parent_config = getattr(current, "parent_config", None)
        configurable = (
            parent_config.get("configurable") if isinstance(parent_config, Mapping) else None
        )
        if not isinstance(configurable, Mapping):
            break
        parent_id = configurable.get("checkpoint_id")
        if (
            not isinstance(parent_id, str)
            or not parent_id
            or parent_id in seen
            or configurable.get("thread_id") != thread_id
            or configurable.get("checkpoint_ns", "") != ns
        ):
            break
        parent = checkpoints.get(parent_id)
        if parent is None:
            break
        history.append(parent)
        seen.add(parent_id)
        current = parent
    return history


async def _selected_subagent_threads(
    checkpointer: Any,
    thread_id: str,
    root: _LoadedThread,
    include_summaries: bool,
    *,
    history: CheckpointHistory,
) -> dict[str, _LoadedThread]:
    """Resolve native child scopes for history or an immutable summary view.

    Summary reads bound child tips to the parent's timestamp so their root
    revision is cacheable. Live trace reads retain independently advancing child
    state; both views require the child's native parent to belong to the lineage.
    """
    checkpoints_by_ns = {ns: checkpoints.values() for ns, checkpoints in history.items() if ns}

    selected = {"": root}
    for ns in sorted(checkpoints_by_ns, key=lambda value: (value.count("|"), value)):
        parent_ns, separator, _leaf = ns.rpartition("|")
        if not separator:
            parent_ns = ""
        parent = selected.get(parent_ns)
        if parent is None or parent.checkpoint_time is None:
            continue
        candidates = [
            checkpoint
            for checkpoint in checkpoints_by_ns[ns]
            if (timestamp := _checkpoint_time(checkpoint)) is not None
            and (not include_summaries or timestamp <= parent.checkpoint_time)
            and _linked_parent_checkpoint(checkpoint, parent_ns) in parent.checkpoint_ids
        ]
        if not candidates:
            continue
        tip = max(candidates, key=_checkpoint_sort_key)
        loaded = await _load_thread(
            checkpointer,
            thread_id,
            ns,
            checkpoint_id=_checkpoint_id(tip),
            include_summaries=include_summaries,
            history=history,
        )
        if loaded is not None:
            selected[ns] = loaded

    subagents: dict[str, _LoadedThread] = {}
    for ns, loaded in selected.items():
        if not ns:
            continue
        parent_ns, separator, leaf = ns.rpartition("|")
        if not separator:
            parent_ns, leaf = "", ns
        parent = selected.get(parent_ns)
        tool_call_id = _task_tool_call_id(leaf, loaded, parent, parent_ns)
        if tool_call_id is not None:
            subagents[tool_call_id] = loaded
    return subagents


def _checkpoint_sort_key(checkpoint: CheckpointTuple) -> tuple[str, str]:
    timestamp = _checkpoint_time(checkpoint)
    return (timestamp.isoformat() if timestamp is not None else "", _checkpoint_id(checkpoint))


def _linked_parent_checkpoint(checkpoint: CheckpointTuple, parent_ns: str) -> str | None:
    parents = (
        checkpoint.metadata.get("parents") if isinstance(checkpoint.metadata, Mapping) else None
    )
    parent_id = parents.get(parent_ns) if isinstance(parents, Mapping) else None
    return parent_id if isinstance(parent_id, str) and parent_id else None


def _task_tool_call_id(
    leaf: str,
    child: _LoadedThread,
    parent: _LoadedThread | None,
    parent_ns: str,
) -> str | None:
    task_id = leaf.partition(":")[2]
    parent_id = _linked_parent_checkpoint(child.history[-1], parent_ns)
    if not task_id or parent is None or parent_id is None:
        return None
    parent_checkpoint = next(
        (checkpoint for checkpoint in parent.history if _checkpoint_id(checkpoint) == parent_id),
        None,
    )
    if parent_checkpoint is None:
        return None
    for write_task, channel, value in parent_checkpoint.pending_writes or ():
        if write_task != task_id or channel != "messages":
            continue
        for message in value if isinstance(value, list) else [value]:
            if isinstance(message, ToolMessage):
                return message.tool_call_id
    return None


def _checkpoint_id(checkpoint: CheckpointTuple) -> str:
    return str(checkpoint.config.get("configurable", {})["checkpoint_id"])


def _conversation_summary(
    occurrence: SummaryOccurrence,
    messages: list[Any],
    scope: dict[str, str],
) -> ConversationSummary | None:
    if occurrence.created_at_index > len(messages):
        return None
    return ConversationSummary(
        id=occurrence.id,
        summary=occurrence.summary,
        created_at_index=occurrence.created_at_index,
        anchor_message_id=_anchor_message_id(messages, occurrence.created_at_index),
        scope=scope,
        committed_at=occurrence.committed_at,
    )


def _anchor_message_id(messages: list[Any], created_at_index: int) -> str | None:
    """The source message immediately before a native creation boundary."""
    for message in reversed(messages[:created_at_index]):
        value = message.get("id") if isinstance(message, Mapping) else getattr(message, "id", None)
        if isinstance(value, str) and value:
            return value
    return None


def _summary_occurrences(history: list[CheckpointTuple], *, ns: str) -> list[SummaryOccurrence]:
    """Recover every committed native event without trusting pending writes."""
    occurrences: list[SummaryOccurrence] = []
    previous_fingerprint: tuple[str, int, int | None] | None = None
    for checkpoint in history:
        values = checkpoint.checkpoint.get("channel_values")
        event = values.get(NATIVE_SUMMARIZATION_EVENT_KEY) if isinstance(values, Mapping) else None
        summary = _summary_text(event)
        created_at_index = _event_integer(event, "created_at_index")
        if summary is None or created_at_index is None or created_at_index < 0:
            continue
        fingerprint = (summary, created_at_index, _event_integer(event, "cutoff_index"))
        if fingerprint == previous_fingerprint:
            continue
        previous_fingerprint = fingerprint
        occurrences.append(
            SummaryOccurrence(
                id=f"summary:{ns or 'root'}:{_checkpoint_id(checkpoint)}",
                summary=summary,
                created_at_index=created_at_index,
                committed_at=_checkpoint_time(checkpoint),
            )
        )
    return occurrences


def _event_integer(event: Any, field: str) -> int | None:
    value = event.get(field) if isinstance(event, Mapping) else getattr(event, field, None)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _summary_text(event: Any) -> str | None:
    message = (
        event.get("summary_message")
        if isinstance(event, Mapping)
        else getattr(event, "summary_message", None)
    )
    content = (
        message.get("content")
        if isinstance(message, Mapping)
        else getattr(message, "content", None)
    )
    return content if isinstance(content, str) and content else None


def _checkpoint_time(checkpoint: CheckpointTuple) -> datetime | None:
    value = checkpoint.checkpoint.get("ts")
    if not isinstance(value, str):
        return None
    try:
        return normalize_utc_datetime(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return None


def _commit_times(history: list[CheckpointTuple]) -> dict[str, datetime]:
    """Message id -> when it was committed.

    A checkpoint's pending writes are the outputs of the step that runs after
    it; they land when the next checkpoint is saved, so that checkpoint's ``ts``
    is when the message was produced.
    """
    times: dict[str, datetime] = {}
    for index, checkpoint in enumerate(history):
        landed = history[min(index + 1, len(history) - 1)].checkpoint["ts"]
        for _task_id, channel, value in checkpoint.pending_writes or ():
            if channel != "messages":
                continue
            for message in value if isinstance(value, list) else [value]:
                message_id = getattr(message, "id", None)
                if message_id and message_id not in times:
                    committed_at = normalize_utc_datetime(
                        datetime.fromisoformat(landed.replace("Z", "+00:00"))
                    )
                    assert committed_at is not None
                    times[message_id] = committed_at
    return times


def _config(thread_id: str, ns: str, checkpoint_id: str | None = None) -> dict[str, Any]:
    configurable: dict[str, Any] = {"thread_id": thread_id, "checkpoint_ns": ns}
    if checkpoint_id is not None:
        configurable["checkpoint_id"] = checkpoint_id
    return {"configurable": configurable}
