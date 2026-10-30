"""Compact, read-only checkpoint history for conversation rendering.

The tuples returned here deliberately contain only transcript-history fields.  They
are native-compatible enough for historical inspection, but do not carry enough
state to resume a graph; callers must use the saver for a selected checkpoint's
live channel state.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any, cast

from langgraph.checkpoint.base import (
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    writes_sort_key,
)
from psycopg import sql

CheckpointHistory = dict[str, dict[str, CheckpointTuple]]

NATIVE_MESSAGES_CHANNEL = "messages"
NATIVE_SUMMARIZATION_EVENT_KEY = "_summarization_event"


async def read_message_checkpoint(
    checkpointer: Any, config: dict[str, Any]
) -> CheckpointTuple | None:
    """Load one checkpoint with only its message channel.

    The Postgres row deliberately retains the checkpoint's native shape so the
    saver can deserialize it through its own tuple loader, while replacing
    inline channel values and blob joins with the single channel required by
    transcript reads.  It is a read projection and must not be used to resume
    graph execution.
    """
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    if not isinstance(checkpointer, AsyncPostgresSaver):
        return await checkpointer.aget_tuple(config)

    configurable = config["configurable"]
    thread_id = configurable["thread_id"]
    checkpoint_ns = configurable.get("checkpoint_ns", "")
    checkpoint_id = configurable.get("checkpoint_id")
    if checkpoint_id:
        args: tuple[Any, ...] = (thread_id, checkpoint_ns, checkpoint_id)
        where = "WHERE thread_id = %s AND checkpoint_ns = %s AND checkpoint_id = %s"
    else:
        args = (thread_id, checkpoint_ns)
        where = "WHERE thread_id = %s AND checkpoint_ns = %s ORDER BY checkpoint_id DESC LIMIT 1"

    query = sql.SQL("""
        SELECT
            thread_id,
            jsonb_set(
                checkpoint,
                '{{channel_values}}',
                COALESCE(
                    (
                        SELECT jsonb_object_agg(channel, value)
                        FROM jsonb_each(checkpoint -> 'channel_values') AS inline(channel, value)
                        WHERE channel = {channel}
                    ),
                    '{{}}'::jsonb
                ),
                true
            ) AS checkpoint,
            checkpoint_ns,
            checkpoint_id,
            parent_checkpoint_id,
            metadata,
            (
                SELECT array_agg(array[bl.channel::bytea, bl.type::bytea, bl.blob])
                FROM checkpoint_blobs AS bl
                WHERE bl.thread_id = checkpoints.thread_id
                  AND bl.checkpoint_ns = checkpoints.checkpoint_ns
                  AND bl.channel = {channel}
                  AND bl.version = checkpoint -> 'channel_versions' ->> {channel}
            ) AS channel_values,
            (
                SELECT array_agg(
                    array[
                        cw.task_id::text::bytea,
                        cw.channel::bytea,
                        cw.type::bytea,
                        cw.blob,
                        convert_to(cw.task_path, 'UTF8'),
                        cw.idx::text::bytea
                    ]
                )
                FROM checkpoint_writes AS cw
                WHERE cw.thread_id = checkpoints.thread_id
                  AND cw.checkpoint_ns = checkpoints.checkpoint_ns
                  AND cw.checkpoint_id = checkpoints.checkpoint_id
                  AND cw.channel = {channel}
            ) AS pending_writes
        FROM checkpoints
        {where}
    """).format(channel=sql.Literal(NATIVE_MESSAGES_CHANNEL), where=sql.SQL(where))
    async with checkpointer._cursor() as cursor:
        await cursor.execute(query, args, binary=True)
        row = await cursor.fetchone()
    return await checkpointer._load_checkpoint_tuple(row) if row is not None else None


async def read_checkpoint_history(
    checkpointer: Any, thread_id: str, *, include_summaries: bool
) -> CheckpointHistory:
    """Read one thread's lightweight, native-compatible checkpoint tuples.

    The Postgres fast path intentionally fetches no complete checkpoint payloads
    or unrelated channel blobs.  Returned tuples are read-only history
    projections, not resumable checkpoints.
    """
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    if not isinstance(checkpointer, AsyncPostgresSaver):
        return await _read_native_history(checkpointer, thread_id)

    rows = await _read_checkpoint_rows(checkpointer, thread_id, include_summaries)
    history, summary_versions = _build_history(rows, thread_id, include_summaries)
    if not history:
        return history

    if include_summaries and summary_versions:
        blobs = await _read_summary_blobs(checkpointer, thread_id, summary_versions)
        _apply_summary_blobs(
            history,
            await asyncio.to_thread(_decode_summary_blob_values, checkpointer, blobs),
        )

    writes = await _read_message_writes(checkpointer, thread_id)
    await _load_message_writes(history, checkpointer, writes)
    return history


async def _read_checkpoint_rows(
    checkpointer: Any, thread_id: str, include_summaries: bool
) -> list[dict[str, Any]]:
    summary_columns = sql.SQL("")
    if include_summaries:
        summary_columns = sql.SQL(""",
                checkpoint -> 'channel_versions' ->> {channel} AS summary_version,
                (checkpoint -> 'channel_values') ? {channel} AS summary_inline_present,
                checkpoint -> 'channel_values' -> {channel} AS summary_inline""").format(
            channel=sql.Literal(NATIVE_SUMMARIZATION_EVENT_KEY)
        )
    query = sql.SQL("""
        SELECT
            checkpoint_ns,
            checkpoint_id,
            parent_checkpoint_id,
            checkpoint ->> 'ts' AS checkpoint_ts,
            metadata -> 'parents' AS parents,
            metadata -> 'lc_agent_name' AS lc_agent_name{summary_columns}
        FROM checkpoints
        WHERE thread_id = %s
        ORDER BY checkpoint_ns, checkpoint_id DESC
    """).format(summary_columns=summary_columns)
    async with checkpointer._cursor() as cursor:
        await cursor.execute(query, (thread_id,), binary=True)
        return cast(list[dict[str, Any]], await cursor.fetchall())


def _build_history(
    rows: list[dict[str, Any]], thread_id: str, include_summaries: bool
) -> tuple[CheckpointHistory, set[tuple[str, str]]]:
    history: CheckpointHistory = {}
    summary_versions: set[tuple[str, str]] = set()
    for row in rows:
        namespace = str(row["checkpoint_ns"])
        checkpoint_id = str(row["checkpoint_id"])
        metadata: dict[str, Any] = {}
        if row["parents"] is not None:
            metadata["parents"] = row["parents"]
        if row["lc_agent_name"] is not None:
            metadata["lc_agent_name"] = row["lc_agent_name"]

        checkpoint: dict[str, Any] = {
            "id": checkpoint_id,
            "ts": row["checkpoint_ts"],
            "channel_values": {},
        }
        if include_summaries:
            channel_values = checkpoint["channel_values"]
            if row["summary_inline_present"]:
                # A JSON null is a stored inline channel value, not absence.
                channel_values[NATIVE_SUMMARIZATION_EVENT_KEY] = row["summary_inline"]
            summary_version = row["summary_version"]
            if summary_version is not None:
                version = str(summary_version)
                checkpoint["channel_versions"] = {NATIVE_SUMMARIZATION_EVENT_KEY: version}
                summary_versions.add((namespace, version))

        configurable = {
            "thread_id": thread_id,
            "checkpoint_ns": namespace,
            "checkpoint_id": checkpoint_id,
        }
        history.setdefault(namespace, {})[checkpoint_id] = CheckpointTuple(
            {"configurable": configurable},
            cast(Checkpoint, checkpoint),
            cast(CheckpointMetadata, metadata),
            (
                {
                    "configurable": {
                        "thread_id": thread_id,
                        "checkpoint_ns": namespace,
                        "checkpoint_id": str(row["parent_checkpoint_id"]),
                    }
                }
                if row["parent_checkpoint_id"] is not None
                else None
            ),
            [],
        )
    return history, summary_versions


async def _read_summary_blobs(
    checkpointer: Any, thread_id: str, versions: set[tuple[str, str]]
) -> list[dict[str, Any]]:
    namespaces, channel_versions = zip(*sorted(versions), strict=True)
    query = sql.SQL("""
        SELECT b.checkpoint_ns, b.version, b.type, b.blob
        FROM checkpoint_blobs AS b
        JOIN unnest(%s::text[], %s::text[]) AS requested(checkpoint_ns, version)
          ON b.checkpoint_ns = requested.checkpoint_ns
         AND b.version = requested.version
        WHERE b.thread_id = %s
          AND b.channel = {channel}
    """).format(channel=sql.Literal(NATIVE_SUMMARIZATION_EVENT_KEY))
    async with checkpointer._cursor() as cursor:
        await cursor.execute(
            query, (list(namespaces), list(channel_versions), thread_id), binary=True
        )
        return cast(list[dict[str, Any]], await cursor.fetchall())


def _decode_summary_blob_values(
    checkpointer: Any, blobs: list[dict[str, Any]]
) -> dict[tuple[str, str], Any]:
    values: dict[tuple[str, str], Any] = {}
    for blob in blobs:
        type_tag = str(blob["type"])
        if type_tag != "empty":
            values[(str(blob["checkpoint_ns"]), str(blob["version"]))] = (
                checkpointer.serde.loads_typed((type_tag, cast(bytes, blob["blob"])))
            )
    return values


def _apply_summary_blobs(history: CheckpointHistory, values: dict[tuple[str, str], Any]) -> None:
    for namespace, checkpoints in history.items():
        for checkpoint in checkpoints.values():
            version = checkpoint.checkpoint.get("channel_versions", {}).get(
                NATIVE_SUMMARIZATION_EVENT_KEY
            )
            key = (namespace, str(version))
            if version is not None and key in values:
                checkpoint.checkpoint["channel_values"][NATIVE_SUMMARIZATION_EVENT_KEY] = values[
                    key
                ]


async def _read_message_writes(checkpointer: Any, thread_id: str) -> list[dict[str, Any]]:
    query = """
        SELECT checkpoint_ns, checkpoint_id, task_id, task_path, idx, type, blob
        FROM checkpoint_writes
        WHERE thread_id = %s
          AND channel = 'messages'
    """
    async with checkpointer._cursor() as cursor:
        await cursor.execute(query, (thread_id,), binary=True)
        return cast(list[dict[str, Any]], await cursor.fetchall())


async def _load_message_writes(
    history: CheckpointHistory, checkpointer: Any, rows: list[dict[str, Any]]
) -> None:
    writes_by_checkpoint: defaultdict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        namespace = str(row["checkpoint_ns"])
        checkpoint_id = str(row["checkpoint_id"])
        if checkpoint_id in history.get(namespace, {}):
            writes_by_checkpoint[(namespace, checkpoint_id)].append(row)
    decoded = await asyncio.to_thread(_decode_message_writes, checkpointer, writes_by_checkpoint)
    for (namespace, checkpoint_id), writes in decoded.items():
        history[namespace][checkpoint_id] = history[namespace][checkpoint_id]._replace(
            pending_writes=writes
        )


def _decode_message_writes(
    checkpointer: Any,
    writes_by_checkpoint: dict[tuple[str, str], list[dict[str, Any]]],
) -> dict[tuple[str, str], list[tuple[str, str, Any]]]:
    return {
        key: [
            (
                str(row["task_id"]),
                "messages",
                checkpointer.serde.loads_typed((str(row["type"]), cast(bytes, row["blob"]))),
            )
            for row in sorted(
                writes,
                key=lambda row: writes_sort_key(
                    str(row["task_path"]), str(row["task_id"]), int(row["idx"])
                ),
            )
        ]
        for key, writes in writes_by_checkpoint.items()
    }


async def _read_native_history(checkpointer: Any, thread_id: str) -> CheckpointHistory:
    """Index a non-Postgres saver's native tuples through its public ``alist`` protocol."""
    history: CheckpointHistory = {}
    async for item in checkpointer.alist({"configurable": {"thread_id": thread_id}}):
        configurable = item.config["configurable"]
        namespace = str(configurable.get("checkpoint_ns", ""))
        checkpoint_id = str(configurable["checkpoint_id"])
        history.setdefault(namespace, {})[checkpoint_id] = item
    return history
