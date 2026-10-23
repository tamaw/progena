"""Storage keys and document helpers for routine graphs.

Routine graphs are persisted as documents in the shared Postgres ``store``
table -- the same one workflow definitions live in (see
``src.progena.workflows``). Two independent clients read/write that table:

- The Progena runtime (async ``LangGraphRuntimeStore``, via
  ``StateManager``) mounts them as ``/routines/<agent_id>.json`` in the
  DeepAgent virtual filesystem (see
  ``src.progena.agent.filesystem.routines.RoutineDirectoryBackend``).
- The FastAPI backend (sync ``PostgresDocumentStore``) reads/writes the same
  keys directly so the Agents page's Graph tab and this module's document
  stay byte-for-byte the same thing the agent edits.

This module only holds the pure, dependency-free pieces (key naming, default
document shape, parse+validate) so both the async runtime side and the sync
backend side can import it without pulling in either store implementation.
"""

from __future__ import annotations

import json
from typing import Any

from src.progena.documents.kinds import ROUTINE_GRAPH_KEY_PREFIX

from .graph import ROUTINE_GRAPH_VERSION, RoutineGraph, validate_routine_graph_payload

DEFAULT_ROUTINE_PROMPT_TEMPLATE = (
    "Complete the delegated task and return the result.\n\nRequest: {request}"
)


def routine_graph_key(agent_id: str) -> str:
    """Return the runtime document key for one agent's routine graph."""
    return f"{ROUTINE_GRAPH_KEY_PREFIX}{agent_id}"


def routine_file_name(agent_id: str) -> str:
    """Return the mounted virtual filename for one routine graph."""
    return f"{agent_id}.json"


def routine_id_from_path(path_or_id: str) -> str:
    """Normalize an agent/routine id from ``<id>``, ``<id>.json``, or ``/routines/...``."""
    normalized = str(path_or_id or "").strip()
    if not normalized:
        raise ValueError("routine id is required")
    if normalized.startswith("/"):
        normalized = normalized.rsplit("/", 1)[-1]
    if normalized.endswith(".json"):
        normalized = normalized[: -len(".json")]
    normalized = normalized.strip()
    if not normalized:
        raise ValueError("routine id is required")
    return normalized


def default_routine_graph(
    agent_id: str, *, name: str | None = None, description: str = ""
) -> dict[str, Any]:
    """Return the simplest possible valid routine graph for a freshly seeded agent."""
    return {
        "id": agent_id,
        "name": name or agent_id,
        "description": description,
        "version": ROUTINE_GRAPH_VERSION,
        "entry": "prompt",
        "policy_id": "default",
        "nodes": [
            {
                "id": "prompt",
                "label": "Prompt",
                "kind": "prompt",
                "output_key": "prompt",
                "config": {"prompt_template": DEFAULT_ROUTINE_PROMPT_TEMPLATE},
            },
        ],
        "edges": [],
    }


def parse_routine_graph_document(content: str) -> dict[str, Any]:
    """Parse and validate a full routine graph JSON document. Raises ``ValueError``."""
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("routine graph document must be a JSON object")
    validated: RoutineGraph = validate_routine_graph_payload(payload)
    serialized = validated.model_dump(mode="json", by_alias=True, exclude_none=True)
    # Validate the representation that will actually be committed, as well
    # as the caller's input: model defaults must never corrupt the contract.
    validate_routine_graph_payload(serialized)
    return serialized
