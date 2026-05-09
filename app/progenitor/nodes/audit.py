"""Operational audit nodes and helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from langgraph.runtime import Runtime

from ..state.models import ProgenitorState
from ..utils import utcnow_isoformat
from .context import HeartbeatContext


async def write_startup_config_audit(
    logs_dir: str | Path,
    config_payload: dict[str, Any],
) -> None:
    """Write the startup configuration audit entry."""
    decisions_path = Path(logs_dir) / "decisions.jsonl"
    decisions_path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "event": "startup_config",
        "timestamp": utcnow_isoformat(),
        "config": config_payload,
    }
    with decisions_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")


async def startup_audit_node(
    _state: ProgenitorState,
    runtime: Runtime[HeartbeatContext],
) -> dict[str, Any]:
    """Write the startup audit entry once for this runtime."""
    context = runtime.context
    if (
        "startup_audit" in context.startup_flags
        or context.audit_logs_dir is None
        or context.startup_audit_payload is None
    ):
        return {}
    await write_startup_config_audit(context.audit_logs_dir, context.startup_audit_payload)
    context.startup_flags.add("startup_audit")
    return {"startup_audit_logged": True}
