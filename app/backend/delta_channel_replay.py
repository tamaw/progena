"""Replay for LangGraph `DeltaChannel`-backed checkpoint channels.

`DeltaChannel` (used by `deepagents>=0.6` for the agent's `messages` state
field) stores only per-turn deltas in `checkpoint_writes`, writing a full
value into `channel_values` only every `snapshot_frequency` turns. A live
graph run reconstructs the current value transparently via LangGraph's own
`channels_from_checkpoint()`. Code that reads checkpoints directly from
Postgres -- as the backend does for the chat list -- has to replay those
deltas itself.

This module isolates the private LangGraph/deepagents surface that replay
depends on (`langgraph._internal._typing.MISSING`,
`deepagents._messages_reducer`). Re-check it whenever `langgraph` or
`deepagents` is upgraded: `DeltaChannel` is explicitly marked beta upstream,
so its API and on-disk shape may still change.

`DeltaChannel` also warns that pruning checkpoints with a `"keep_latest"`
strategy can sever the ancestor chain this replay depends on, silently
reconstructing as an empty list with no error raised. Nothing in this repo
prunes checkpoints today (see `app/progenitor/database/langgraph.py`); if
that changes, this module needs revisiting alongside it.
"""

from __future__ import annotations

from typing import Any

from deepagents._messages_reducer import _messages_delta_reducer
from langgraph._internal._typing import MISSING
from langgraph.channels.delta import DeltaChannel
from langgraph.checkpoint.base import DeltaChannelHistory


def replay_delta_messages(entry: DeltaChannelHistory) -> list[Any]:
    """Reconstruct a `messages`-shaped `DeltaChannel` value from its history.

    `entry` is one channel's value from `saver.get_delta_channel_history()`.
    """
    # snapshot_frequency only gates when the *write* path emits a snapshot
    # blob; from_checkpoint()/replay_writes() never read it, so any positive
    # value reconstructs the same messages.
    channel = DeltaChannel(
        _messages_delta_reducer,
        list,
        snapshot_frequency=1,
    ).from_checkpoint(entry.get("seed", MISSING))
    channel.replay_writes(entry["writes"])
    return list(channel.value)
