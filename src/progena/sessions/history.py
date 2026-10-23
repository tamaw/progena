"""Load a chat's messages from the LangGraph checkpointer for rendering.

Messages are rebuilt the way ``aget_state`` does (``achannels_from_checkpoint``),
which also covers the main agent's delta-encoded ``messages`` channel, where
the latest checkpoint may hold no value or a ``_DeltaSnapshot``. It runs per
checkpoint namespace because subagents started by the ``task`` tool checkpoint
under their own ``tools:<task_id>`` namespace, which ``aget_state`` only exposes
while that subagent is mid-run.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from deepagents.graph import DeepAgentState
from langchain_core.messages import ToolMessage
from langgraph.checkpoint.base import CheckpointTuple
from langgraph.graph import StateGraph
from langgraph.pregel._checkpoint import achannels_from_checkpoint

from .transcript import DEFAULT_AGENT_NICK, ThreadMessages

# The main agent's ``messages`` channel spec: a DeltaChannel. Subagent
# namespaces store plain lists, which the same spec restores as-is.
_MESSAGES_SPECS = {"messages": StateGraph(DeepAgentState).channels["messages"]}


@dataclass(frozen=True)
class Conversation:
    """A chat's root thread, the subagent threads it delegated to, and its newest checkpoint."""

    root: ThreadMessages
    subagents: dict[str, ThreadMessages]
    checkpoint_id: str


async def latest_checkpoint_id(checkpointer: Any, thread_id: str) -> str | None:
    """The chat's newest root checkpoint id, or ``None`` if it has never run."""
    tip = await checkpointer.aget_tuple(_config(thread_id, ""))
    return str(tip.config["configurable"]["checkpoint_id"]) if tip is not None else None


async def load_conversation(checkpointer: Any, thread_id: str) -> Conversation | None:
    """Load the chat's messages, subagent transcripts and commit times."""
    root = await _load_thread(checkpointer, thread_id, "")
    if root is None:
        return None
    thread, checkpoint_id = root
    subagents: dict[str, ThreadMessages] = {}
    for tool_call_id, ns in (await _subagent_namespaces(checkpointer, thread_id)).items():
        loaded = await _load_thread(checkpointer, thread_id, ns)
        if loaded is not None:
            subagents[tool_call_id] = loaded[0]
    return Conversation(root=thread, subagents=subagents, checkpoint_id=checkpoint_id)


async def _load_thread(
    checkpointer: Any, thread_id: str, ns: str
) -> tuple[ThreadMessages, str] | None:
    history = [t async for t in checkpointer.alist(_config(thread_id, ns))]  # newest first
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
    return thread, str(tip.config["configurable"]["checkpoint_id"])


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
                    times[message_id] = datetime.fromisoformat(landed)
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
