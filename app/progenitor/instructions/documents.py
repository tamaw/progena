"""Pure rules for instruction documents (front matter, ownership, rendering).

Instructions are markdown files under ``data/instructions/<group>/``. Each starts
with YAML front matter::

    ---
    title: Progenitor system prompt
    description: What the instruction is for.
    variables: [tool_context]      # optional: {placeholders} the code fills in
    ---

The first path segment is the group: the agent that reads the instruction. The
Progenitor runtime reads the ``progenitor`` group from Postgres, so those are
editable. Every other group is bundled with its A2A agent, which reads its own
copy from disk; the stored copy is a read-only mirror of that file.
"""

from __future__ import annotations

import re
from typing import Any

from ..front_matter import split_front_matter

RUNTIME_INSTRUCTION_GROUP = "progenitor"
GENESIS_INSTRUCTION = "progenitor/genesis.md"
_VARIABLE_RE = re.compile(r"^[a-z_][a-z0-9_]*$")
_FRONT_MATTER_ERROR = (
    "instructions need YAML front matter with a non-empty 'title' and 'description', e.g.\n"
    "---\ntitle: ...\ndescription: ...\nvariables: [name]\n---"
)


def instruction_group(path: str) -> str:
    """Return the agent group an instruction path belongs to (its first segment)."""
    return path.split("/", 1)[0] if "/" in path else ""


def is_runtime_instruction(path: str) -> bool:
    """Whether the Progenitor runtime reads this instruction from Postgres."""
    return instruction_group(path) == RUNTIME_INSTRUCTION_GROUP


def parse_instruction(content: str) -> tuple[dict[str, Any], str]:
    """Return ``(front_matter, body)`` for a valid instruction; raise ``ValueError`` otherwise."""
    front_matter, body = split_front_matter(content)
    if front_matter is None:
        raise ValueError(_FRONT_MATTER_ERROR)
    for field in ("title", "description"):
        value = front_matter.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(_FRONT_MATTER_ERROR)
    variables = front_matter.get("variables", [])
    if not isinstance(variables, list) or not all(
        isinstance(name, str) and _VARIABLE_RE.fullmatch(name) for name in variables
    ):
        raise ValueError("'variables' must be a list of lowercase snake_case placeholder names")
    missing = [name for name in variables if f"{{{name}}}" not in body]
    if missing:
        raise ValueError(
            "the body no longer contains "
            + ", ".join(f"{{{name}}}" for name in missing)
            + "; keep every declared variable (the code fills it in) or remove it from 'variables'"
        )
    return front_matter, body


def render_instruction(instruction: str, /, **replacements: object) -> str:
    """Return the instruction body with ``{placeholder}`` replacements applied."""
    _, body = parse_instruction(instruction)
    for key, value in replacements.items():
        body = body.replace(f"{{{key}}}", str(value))
    return body
