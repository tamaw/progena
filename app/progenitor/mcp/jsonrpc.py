"""Helpers for parsing MCP JSON-RPC responses."""

from __future__ import annotations

import json
from typing import Any, cast


def parse_mcp_jsonrpc_response(text: str) -> dict[str, Any] | None:
    """Parse a JSON-RPC response delivered as plain JSON or SSE."""
    if not isinstance(text, str):
        return None
    raw = text.strip()
    if not raw:
        return None

    candidates: list[str] = []
    if raw.startswith("{"):
        candidates.append(raw)
    else:
        for line in raw.splitlines():
            stripped = line.strip()
            if not stripped.startswith("data:"):
                continue
            payload = stripped[len("data:") :].strip()
            if payload and payload != "[DONE]":
                candidates.append(payload)

    parsed: dict[str, Any] | None = None
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            parsed = cast(dict[str, Any], data)

    return parsed
