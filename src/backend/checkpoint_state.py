"""Read checkpoint state without depending on a running runtime."""

from __future__ import annotations

import binascii
from typing import Any

from src.backend.storage.documents import get_default_document_store, store_database_url


def _task_wrote(
    checkpoint: dict[str, Any],
    metadata: dict[str, Any],
    pending_writes: list[Any],
    node: str,
    *,
    trigger: str,
    send_index: int | None = None,
) -> bool:
    """Whether LangGraph already has output writes for this pending task."""
    checkpoint_id = checkpoint.get("id")
    if not isinstance(checkpoint_id, str):
        return False
    try:
        checkpoint_id_bytes = binascii.unhexlify(checkpoint_id.replace("-", ""))
        step = int(metadata.get("step", -1)) + 1
        from langgraph.pregel._algo import _uuid5_str, _xxhash_str
        from langgraph.pregel._task_status import CONTROL_WRITES

        task_id_fn = _xxhash_str if checkpoint.get("v", 0) > 1 else _uuid5_str
        task_id = task_id_fn(
            checkpoint_id_bytes,
            node,
            str(step),
            node,
            "__pregel_push" if send_index is not None else "__pregel_pull",
            str(send_index) if send_index is not None else trigger,
        )
    except (ImportError, TypeError, ValueError):
        return False
    return any(
        isinstance(write, tuple)
        and len(write) >= 2
        and write[0] == task_id
        and write[1] not in CONTROL_WRITES
        for write in pending_writes
    )


def _next_nodes(
    checkpoint: dict[str, Any], metadata: dict[str, Any], pending_writes: list[Any]
) -> list[str]:
    """Rebuild StateGraph's pending PULL and PUSH task names from a checkpoint.

    This follows ``prepare_next_tasks``: PUSH ``__pregel_tasks`` are first,
    then ``branch:to:<node>`` PULL triggers newer than ``versions_seen``. A
    task with recorded output writes is not pending, matching ``get_state``.
    """
    values = checkpoint.get("channel_values")
    versions = checkpoint.get("channel_versions")
    seen_by_node = checkpoint.get("versions_seen")
    if (
        not isinstance(values, dict)
        or not isinstance(versions, dict)
        or not isinstance(seen_by_node, dict)
    ):
        return []

    next_nodes: list[str] = []
    sends = values.get("__pregel_tasks")
    if isinstance(sends, list):
        for index, send in enumerate(sends):
            node = getattr(send, "node", None)
            if not isinstance(node, str) and isinstance(send, dict):
                node = send.get("node")
            if isinstance(node, str) and not _task_wrote(
                checkpoint, metadata, pending_writes, node, trigger="", send_index=index
            ):
                next_nodes.append(node)

    for channel, version in versions.items():
        if (
            not isinstance(channel, str)
            or not channel.startswith("branch:to:")
            or channel not in values
        ):
            continue
        node = channel.removeprefix("branch:to:")
        seen = seen_by_node.get(node)
        seen_version = seen.get(channel) if isinstance(seen, dict) else None
        try:
            pending = seen_version is None or version > seen_version
        except TypeError:
            pending = False
        if pending and not _task_wrote(checkpoint, metadata, pending_writes, node, trigger=channel):
            next_nodes.append(node)
    return next_nodes


def checkpoint_state(checkpoint_tuple: Any | None) -> dict[str, Any]:
    """Return the latest root checkpoint's state fields used by the backend."""
    state: dict[str, Any] = {
        "exists": False,
        "next": [],
        "waiting": False,
        "failed": False,
        "checkpoint_id": None,
        "updated_at": None,
        "runtime_instance": None,
    }
    if checkpoint_tuple is None:
        return state

    checkpoint = getattr(checkpoint_tuple, "checkpoint", None)
    if not isinstance(checkpoint, dict):
        return state
    pending_writes = list(getattr(checkpoint_tuple, "pending_writes", None) or [])
    raw_metadata = getattr(checkpoint_tuple, "metadata", None)
    metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
    state.update(
        exists=True,
        next=_next_nodes(checkpoint, metadata, pending_writes),
        waiting=any(
            isinstance(write, tuple) and len(write) >= 2 and write[1] == "__interrupt__"
            for write in pending_writes
        ),
        failed=any(
            isinstance(write, tuple) and len(write) >= 2 and write[1] == "__error__"
            for write in pending_writes
        ),
        checkpoint_id=checkpoint.get("id") if isinstance(checkpoint.get("id"), str) else None,
        updated_at=checkpoint.get("ts") if isinstance(checkpoint.get("ts"), str) else None,
    )
    if isinstance(metadata.get("runtime_instance"), str):
        state["runtime_instance"] = metadata["runtime_instance"]
    return state


async def read_latest_checkpoint(thread_id: str, doc_store=None) -> Any | None:
    """Read one thread's latest root checkpoint through the shared Postgres URL."""
    store = doc_store if doc_store is not None else get_default_document_store()
    database_url = store_database_url(store)
    if not database_url:
        return None

    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    async with AsyncPostgresSaver.from_conn_string(database_url) as saver:
        return await saver.aget_tuple({"configurable": {"thread_id": thread_id}})


async def read_thread_checkpoint_state(thread_id: str, doc_store=None) -> dict[str, Any]:
    """Read the backend fields for one thread's latest root checkpoint."""
    return checkpoint_state(await read_latest_checkpoint(thread_id, doc_store))
