"""Who owns an instruction, and what an edit may change.

Instructions are ``instruction:<group>/<file>.md`` documents in the
:mod:`templates` format (optional ``title``/``description``/``inputs``/
``output_schema``/``system`` front matter, ``{placeholder}`` body). The first
path segment is the group:

* ``progenitor/`` is read by the runtime, which fills each placeholder from
  code, so edits must keep the same placeholders;
* any other group (e.g. ``workflows/``) is a template that workflow nodes
  reference, freely editable by the user and the agent.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from .templates import (
    check_template_type_refs,
    instruction_relative_path,
    parse_instruction_template,
)

if TYPE_CHECKING:
    from src.progenitor.documents.types import DocumentType

RUNTIME_INSTRUCTION_GROUP = "progenitor"
SYSTEM_INSTRUCTION = "progenitor/SYSTEM.md"


def instruction_group(path: str) -> str:
    """The group an instruction path belongs to (its first segment, or ``""``)."""
    return path.split("/", 1)[0] if "/" in path else ""


def is_runtime_instruction(path: str) -> bool:
    """Whether the Progenitor runtime reads this instruction and fills it from code."""
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
        expected = parse_instruction_template(previous).roots
        if sorted(roots) != sorted(expected):
            raise ValueError(
                "the runtime fills this instruction's placeholders from code; keep exactly "
                + ", ".join(f"{{{name}}}" for name in sorted(expected))
                if expected
                else "the runtime fills this instruction from code; it takes no {placeholders}"
            )


def render_instruction(instruction: str, /, **replacements: object) -> str:
    """The instruction body with ``{name}`` placeholders replaced by ``replacements``."""
    body = parse_instruction_template(instruction).body
    for key, value in replacements.items():
        body = body.replace(f"{{{key}}}", str(value))
    return body
