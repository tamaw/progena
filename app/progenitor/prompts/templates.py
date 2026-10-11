"""Prompt template parsing, rendering, and type resolution.

Prompt documents support optional YAML front matter:

    ---
    title: Card moved
    description: Notify about a card move
    inputs:
      card: kanban_card
    output_schema: kanban_card
    system: You are terse.
    ---
    Card "{card.title}" moved to {card.column_id}.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import yaml

from app.progenitor.document_types import DocumentType
from app.progenitor.schemas import load_schema

_PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)\}")


@dataclass(frozen=True)
class PromptTemplate:
    """Frozen representation of a parsed prompt template."""

    title: str | None
    description: str | None
    inputs: dict[str, str | dict]
    output_schema: str | dict | None
    system: str | None
    body: str
    placeholders: list[str]
    roots: list[str]


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split ``---``-delimited YAML frontmatter from a markdown body."""
    stripped = text.lstrip("\n")
    if not stripped.startswith("---"):
        return {}, text
    lines = stripped.split("\n")
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        raise ValueError("frontmatter opened with '---' but never closed")
    try:
        data = yaml.safe_load("\n".join(lines[1:end])) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid YAML frontmatter: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("frontmatter must be a mapping")
    return data, "\n".join(lines[end + 1 :])


def parse_prompt_template(text: str) -> PromptTemplate:
    """Parse a prompt document into a :class:`PromptTemplate`.

    Raises:
        ValueError: on malformed front matter, unknown keys, or ``inputs`` keys
        that are never used as placeholder roots in the body.
    """
    frontmatter, body = _split_frontmatter(text)

    allowed_keys = {"title", "description", "inputs", "output_schema", "system"}
    unknown = set(frontmatter.keys()) - allowed_keys
    if unknown:
        raise ValueError(f"unknown front matter key(s): {', '.join(sorted(unknown))}")

    title = frontmatter.get("title")
    if title is not None and not isinstance(title, str):
        raise ValueError("front matter 'title' must be a string")

    description = frontmatter.get("description")
    if description is not None and not isinstance(description, str):
        raise ValueError("front matter 'description' must be a string")

    raw_inputs = frontmatter.get("inputs", {})
    if not isinstance(raw_inputs, dict):
        raise ValueError("front matter 'inputs' must be a mapping")
    inputs: dict[str, str | dict] = {}
    for k, v in raw_inputs.items():
        if not isinstance(k, str):
            raise ValueError(f"front matter 'inputs' key must be a string: {k!r}")
        if not isinstance(v, (str, dict)):
            raise ValueError(
                f"front matter 'inputs' value must be a type ref string or inline schema dict: {v!r}"
            )
        inputs[k] = v

    output_schema = frontmatter.get("output_schema")
    if output_schema is not None and not isinstance(output_schema, (str, dict)):
        raise ValueError(
            "front matter 'output_schema' must be a type ref string or inline schema dict"
        )

    system = frontmatter.get("system")
    if system is not None and not isinstance(system, str):
        raise ValueError("front matter 'system' must be a string")

    placeholders: list[str] = []
    seen_roots: set[str] = set()
    roots: list[str] = []
    for match in _PLACEHOLDER_RE.finditer(body):
        dotted = match.group(1)
        if dotted not in placeholders:
            placeholders.append(dotted)
        root = dotted.split(".", 1)[0]
        if root == "thread_id":
            continue
        if root not in seen_roots:
            seen_roots.add(root)
            roots.append(root)

    unused = set(inputs.keys()) - set(roots)
    if unused:
        raise ValueError(
            f"front matter 'inputs' key(s) not used as placeholder roots: {', '.join(sorted(unused))}"
        )

    return PromptTemplate(
        title=title,
        description=description,
        inputs=inputs,
        output_schema=output_schema,
        system=system,
        body=body,
        placeholders=placeholders,
        roots=roots,
    )


def render_prompt_template(template: PromptTemplate, values: Mapping[str, Any]) -> str:
    """Render *template* by replacing each placeholder with the value at its
    dotted path in *values*.

    Strings are inserted verbatim; everything else is serialised with
    ``json.dumps(..., indent=2, sort_keys=True, default=str)``.  A missing
    path raises ``ValueError("missing value for {path}")``.

    When the template carries a ``system`` section it is prepended as its own
    leading section.
    """

    def _replacer(match: re.Match) -> str:
        dotted = match.group(1)
        keys = dotted.split(".")
        val: Any = values
        for key in keys:
            if isinstance(val, Mapping):
                if key not in val:
                    raise ValueError(f"missing value for {{{dotted}}}")
                val = val[key]
            else:
                raise ValueError(f"missing value for {{{dotted}}}")
        return (
            str(val)
            if isinstance(val, str)
            else json.dumps(val, indent=2, sort_keys=True, default=str)
        )

    result = _PLACEHOLDER_RE.sub(_replacer, template.body)

    if template.system:
        result = f"{template.system}\n\n{result}"

    return result


def resolve_type_ref(ref: str | dict, document_types: Mapping[str, DocumentType]) -> dict:
    """Return the JSON schema described by *ref*.

    A type ref is:

    * a document type id (e.g. ``kanban_card``) – the returned schema is the
      document type's schema with ``title`` set to the type's label;
    * a schema file name under ``data/schemas/`` (e.g.
      ``schedule_entry.schema.v1.json`` or just ``schedule_entry``); or
    * an inline JSON-schema dict (returned as-is).

    Raises:
        ValueError: if the reference cannot be resolved.
    """
    if isinstance(ref, dict):
        return dict(ref)

    if ref in document_types:
        doc_type = document_types[ref]
        schema = doc_type.load_schema()
        from app.progenitor.workflows import _typed_document_schema

        embedded, _definitions = _typed_document_schema(doc_type, schema)
        return embedded

    try:
        return load_schema(ref)
    except FileNotFoundError:
        raise ValueError(f"unknown type ref: {ref!r}") from None
