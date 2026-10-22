"""Validation capabilities."""

from __future__ import annotations

import json

from src.progenitor.documents.catalog import check_entry_schema
from src.progenitor.documents.values import JsonDoc


def validate_registry_document(content: str) -> JsonDoc:
    """Parse + validate an ``agents.json`` payload (the ``registry`` document).

    Shape: ``{"agents": [...], "tools": [...]}``, no other top-level keys.
    Each agent's shape mirrors ``data/schemas/agent_entry.schema.v1.json``.
    ``tools`` is runtime-managed (MCP-derived); this only checks its shape, not
    its contents.
    """
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("top level must be a JSON object")
    extra_keys = set(data) - {"agents", "tools"}
    if extra_keys:
        raise ValueError(f"unexpected top-level key(s): {', '.join(sorted(extra_keys))}")

    agents = data.get("agents")
    if not isinstance(agents, list):
        raise ValueError("missing 'agents' array")
    for index, agent in enumerate(agents):
        if not isinstance(agent, dict):
            raise ValueError(f"agent {index} must be an object")
        check_entry_schema(f"agent {index}", agent, "agent_entry")
        if not str(agent.get("name") or "").strip():
            raise ValueError(f"agent {index} is missing 'name'")

    tools = data.get("tools")
    if not isinstance(tools, list):
        raise ValueError("missing 'tools' array")

    return data
