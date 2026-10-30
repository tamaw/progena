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

from src.progena.utils import normalize_utc_datetime

from .transcript import DEFAULT_AGENT_NICK, ThreadMessages

# The main agent's ``messages`` channel spec: a DeltaChannel. Subagent
# namespaces store plain lists, which the same spec restores as-is.
_MESSAGES_SPECS = {"messages": StateGraph(DeepAgentState).channels["messages"]}


_NATIVE_SUMMARIZATION_EVENT_KEY = "_summarization_event"


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


async def load_conversation(
    checkpointer: Any,
    thread_id: str,
    *,
    checkpoint_id: str | None = None,
    include_summaries: bool = False,
    include_subagents: bool = True,
) -> Conversation | None:
    """Load the chat's committed messages, and optionally native summaries."""
    root = await _load_thread(
        checkpointer,
        thread_id,
        "",
        checkpoint_id=checkpoint_id,
        include_summaries=include_summaries,
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
        for tool_call_id, ns in (await _subagent_namespaces(checkpointer, thread_id)).items():
            loaded = await _load_thread(
                checkpointer, thread_id, ns, include_summaries=include_summaries
            )
            if loaded is None:
                continue
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
    checkpoint_id: str | None = None,
    include_summaries: bool = False,
) -> _LoadedThread | None:
    history = [t async for t in checkpointer.alist(_config(thread_id, ns))]  # newest first
    if checkpoint_id is not None:
        checkpoint_index = next(
            (
                index
                for index, checkpoint in enumerate(history)
                if _checkpoint_id(checkpoint) == checkpoint_id
            ),
            None,
        )
        if checkpoint_index is None:
            return None
        history = history[checkpoint_index:]
    if not history:
        return None
    tip = history[0]
    channels, _ = await achannels_from_checkpoint(
        _MESSAGES_SPECS, tip.checkpoint, saver=checkpointer, config=tip.config
    )
    messages = channels["messages"].get() if channels["messages"].is_available() else []
    thread = ThreadMessages(
        messages=list(messages),
        committed_at=_commit_times(history[::-1]),
        # An unnamed subagent namespace gets "" so the renderer uses the task's subagent_type.
        nick=str(tip.metadata.get("lc_agent_name") or ("" if ns else DEFAULT_AGENT_NICK)),
    )
    return _LoadedThread(
        thread=thread,
        checkpoint_id=_checkpoint_id(tip),
        summaries=tuple(_summary_occurrences(history[::-1], ns=ns)) if include_summaries else (),
    )


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
        event = values.get(_NATIVE_SUMMARIZATION_EVENT_KEY) if isinstance(values, Mapping) else None
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


async def _subagent_namespaces(checkpointer: Any, thread_id: str) -> dict[str, str]:
    """Map each ``task`` tool_call_id to the subagent namespace it ran in.

    A subagent's namespace ends in ``tools:<pregel task id>``; its parent
    checkpoint (``metadata.parents``) holds that task's pending ``messages``
    write: the ToolMessage carrying the originating ``tool_call_id``.
    """
    first_seen: dict[str, CheckpointTuple] = {}
    async for checkpoint in checkpointer.alist({"configurable": {"thread_id": thread_id}}):
        ns = checkpoint.config["configurable"]["checkpoint_ns"]
        if ns:
            first_seen[ns] = checkpoint  # alist is newest first; keep the oldest.
    links: dict[str, str] = {}
    for ns, checkpoint in first_seen.items():
        parent_ns, _, leaf = ns.rpartition("|")
        task_id = leaf.partition(":")[2]
        parent_id = (checkpoint.metadata.get("parents") or {}).get(parent_ns)
        if not task_id or not parent_id:
            continue
        parent = await checkpointer.aget_tuple(_config(thread_id, parent_ns, parent_id))
        for write_task, channel, value in (parent.pending_writes if parent else None) or ():
            if write_task != task_id or channel != "messages":
                continue
            for message in value if isinstance(value, list) else [value]:
                if isinstance(message, ToolMessage):
                    links[message.tool_call_id] = ns
    return links


def _config(thread_id: str, ns: str, checkpoint_id: str | None = None) -> dict[str, Any]:
    configurable: dict[str, Any] = {"thread_id": thread_id, "checkpoint_ns": ns}
    if checkpoint_id is not None:
        configurable["checkpoint_id"] = checkpoint_id
    return {"configurable": configurable}
