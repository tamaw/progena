"""Who owns an instruction, and what an edit may change.

Instructions are ``instruction:<group>/<file>.md`` documents in the
:mod:`templates` format (optional ``title``/``description``/``inputs``/
``output_schema``/``system`` front matter, ``{placeholder}`` body). The first
path segment is the group:

* ``progena/`` is read by the runtime, which fills each placeholder from
  code, so edits must keep the same placeholders; ``{agent_name}`` (the
  configured agent display name) is always filled and may be added or removed;
* any other group (e.g. ``workflows/``) is a template that workflow nodes
  reference, freely editable by the user and the agent.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from .templates import (
    check_template_type_refs,
    instruction_relative_path,
    parse_instruction_template,
)

if TYPE_CHECKING:
    from src.progena.documents.types import DocumentType

RUNTIME_INSTRUCTION_GROUP = "progena"
GENESIS_INSTRUCTION = "progena/genesis.md"
AGENT_NAME_PLACEHOLDER = "agent_name"


def instruction_group(path: str) -> str:
    """The group an instruction path belongs to (its first segment, or ``""``)."""
    return path.split("/", 1)[0] if "/" in path else ""


def is_runtime_instruction(path: str) -> bool:
    """Whether the Progena runtime reads this instruction and fills it from code."""
    return instruction_group(path) == RUNTIME_INSTRUCTION_GROUP


def check_instruction_write(
    path: str,
    previous: str | None,
    content: str,
    document_types: Mapping[str, DocumentType],
) -> None:
    """Raise ``ValueError`` unless ``content`` may replace ``previous`` at ``path``.

    Front matter types are resolved against ``document_types`` here, so a bad
    type fails the write that introduced it rather than a later workflow save.
    """
    instruction_relative_path(path)
    template = parse_instruction_template(content)
    check_template_type_refs(template, document_types)
    roots = template.roots
    if is_runtime_instruction(path) and previous is not None:
        roots = [root for root in roots if root != AGENT_NAME_PLACEHOLDER]
        expected = [
            root
            for root in parse_instruction_template(previous).roots
            if root != AGENT_NAME_PLACEHOLDER
        ]
        if sorted(roots) != sorted(expected):
            raise ValueError(
                "the runtime fills this instruction's placeholders from code; keep exactly "
                + ", ".join(f"{{{name}}}" for name in sorted(expected))
                + " ({agent_name} is optional)"
                if expected
                else "the runtime fills this instruction from code; it takes no {placeholders} "
                "other than the optional {agent_name}"
            )


def render_instruction(instruction: str, /, **replacements: object) -> str:
    """The instruction body with ``{name}`` placeholders replaced by ``replacements``."""
    body = parse_instruction_template(instruction).body
    for key, value in replacements.items():
        body = body.replace(f"{{{key}}}", str(value))
    return body


async def render_runtime_instruction(
    state_manager: Any, instruction: str, /, **replacements: object
) -> str:
    """Render a runtime instruction, filling ``{agent_name}`` from preferences."""
    from src.progena.settings.preferences import agent_display_name

    preferences = await state_manager.get_preferences_config()
    return render_instruction(
        instruction,
        **{AGENT_NAME_PLACEHOLDER: agent_display_name(preferences), **replacements},
    )
