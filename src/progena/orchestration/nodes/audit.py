"""Operational audit nodes and helpers."""

from __future__ import annotations

from typing import Any

from langgraph.runtime import Runtime

from src.progena.orchestration.nodes.context import HeartbeatContext
from src.progena.orchestration.state import ProgenaState
from src.progena.utils import utcnow_isoformat


async def write_startup_config_audit_to_sink(
    log_sink: Any,
    config_payload: dict[str, Any],
) -> None:
    """Write the startup configuration audit entry to the configured log sink."""
    entry = {
        "event": "startup_config",
        "timestamp": utcnow_isoformat(),
        "config": config_payload,
    }
    await log_sink.write_log(
        source="decisions",
        message="Startup configuration",
        level="info",
        payload=entry,
        timestamp=entry["timestamp"],
        relative_path="decisions",
    )


async def startup_audit_node(
    _state: ProgenaState,
    runtime: Runtime[HeartbeatContext],
) -> dict[str, Any]:
    """Write the startup audit entry once for this runtime."""
    context = runtime.context
    if (
        "startup_audit" in context.startup_flags
        or context.startup_audit_payload is None
        or context.audit_log_sink is None
    ):
        return {}
    await write_startup_config_audit_to_sink(context.audit_log_sink, context.startup_audit_payload)
    context.startup_flags.add("startup_audit")
    return {"startup_audit_logged": True}
