"""Operational startup logging."""

from __future__ import annotations

import json
import logging
from typing import Any

from langgraph.runtime import Runtime

from src.progena.orchestration.nodes.context import HeartbeatContext
from src.progena.orchestration.state import ProgenaState
from src.progena.security.redaction import redact_secrets

logger = logging.getLogger(__name__)


async def startup_audit_node(
    _state: ProgenaState,
    runtime: Runtime[HeartbeatContext],
) -> dict[str, Any]:
    """Log the redacted startup configuration once for this runtime."""
    context = runtime.context
    if "startup_audit" in context.startup_flags or context.startup_audit_payload is None:
        return {}
    logger.info(
        "Startup configuration: %s",
        redact_secrets(
            json.dumps(
                context.startup_audit_payload, separators=(",", ":"), sort_keys=True, default=str
            )
        ),
    )
    context.startup_flags.add("startup_audit")
    return {"startup_audit_logged": True}
