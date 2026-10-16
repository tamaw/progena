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

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

import yaml

from app.progenitor.document_types import DocumentType
from app.progenitor.schemas import load_schema

_PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)\}")


def prompt_relative_path(path: str) -> str:
    """Validate a prompt document path relative to the prompts root.

    Prompt references, the prompt API and the agent's ``/prompts/`` mount all
    name a prompt by this path (``workflows/card.md``): relative, no ``..``,
    markdown only.
    """

    candidate = PurePosixPath(path)
    if not path or path.endswith("/") or candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError(f"Invalid prompt path '{path}'")
    if candidate.suffix.lower() != ".md":
        raise ValueError(f"Prompt paths must be markdown (.md): '{path}'")
    return candidate.as_posix()


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
    lines = stripped.split("\n")
    if lines[0].rstrip() != "---":
        return {}, text
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

    for key in frontmatter:
        if not isinstance(key, str):
            raise ValueError(f"front matter keys must be strings, got {key!r}")

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


def parse_format_template(text: str) -> PromptTemplate:
    """Parse a plain-text format template without allowing front matter."""

    template = parse_prompt_template(text)
    if template.body != text:
        raise ValueError("format template must not contain front matter")
    return template


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


_SCHEMA_DEFINITION_KEYS = ("$defs", "definitions")


def _json_pointer(root: Mapping[str, Any], ref: str) -> Any:
    """The value a local ``#/...`` reference points to within ``root``."""

    node: Any = root
    for raw in ref.removeprefix("#/").split("/"):
        part = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(node, Mapping) and part in node:
            node = node[part]
        elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
            node = node[int(part)]
        else:
            raise ValueError(f"schema reference {ref!r} does not resolve")
    return node


def _split_definitions(schema: Mapping[str, Any]) -> tuple[dict, dict[str, dict]]:
    """Separate root ``$defs``/``definitions`` from an embeddable schema copy.

    The copy is embedded under a composed root, where only references into
    the root definitions still resolve; any other local reference (e.g.
    ``#/properties/name``) has its target hoisted into ``$defs`` and is
    rewritten to point there.
    """

    embedded = {key: value for key, value in schema.items() if key not in ("$schema", "$id")}
    definitions = {
        key: embedded.pop(key)
        for key in _SCHEMA_DEFINITION_KEYS
        if isinstance(embedded.get(key), dict)
    }
    hoisted: dict[str, Any] = {}

    def rewrite(node: Any) -> Any:
        if isinstance(node, list):
            return [rewrite(item) for item in node]
        if not isinstance(node, Mapping):
            return node
        rewritten = {key: rewrite(value) for key, value in node.items()}
        ref = node.get("$ref")
        if (
            not isinstance(ref, str)
            or not ref.startswith("#")
            or ref.startswith(tuple(f"#/{key}/" for key in _SCHEMA_DEFINITION_KEYS))
        ):
            return rewritten
        if ref in ("#", "#/"):
            raise ValueError("an embedded schema cannot reference its own root")
        target = _json_pointer(schema, ref)
        name = (
            "ref_"
            + hashlib.sha1(  # noqa: S324 - a content name, not security
                json.dumps(target, sort_keys=True, default=str).encode(), usedforsecurity=False
            ).hexdigest()[:12]
        )
        if name not in hoisted:
            hoisted[name] = {}  # reserve first: the target may reference itself
            hoisted[name] = rewrite(target)
        rewritten["$ref"] = f"#/$defs/{name}"
        return rewritten

    embedded = rewrite(embedded)
    definitions = {key: rewrite(value) for key, value in definitions.items()}
    if hoisted:
        definitions["$defs"] = {**definitions.get("$defs", {}), **hoisted}
    return embedded, definitions


def resolve_type_ref(
    ref: str | dict, document_types: Mapping[str, DocumentType]
) -> tuple[dict, dict[str, dict]]:
    """Return ``(schema, root definitions)`` for the type *ref* describes.

    A type ref is:

    * a document type id (e.g. ``kanban_card``) – the document type's schema
      with ``title`` set to the type's label;
    * a schema file name under ``data/schemas/`` (e.g.
      ``schedule_entry.schema.v1.json`` or just ``schedule_entry``); or
    * an inline JSON-schema dict.

    The schema is embedded as a property of a composed port schema, where
    ``#/$defs/...`` refers to the composed root, so its definitions are
    returned separately for the caller to place there.

    Raises:
        ValueError: if the reference cannot be resolved.
    """
    if isinstance(ref, dict):
        return _split_definitions(ref)

    if ref in document_types:
        doc_type = document_types[ref]
        from app.progenitor.workflows import _typed_document_schema

        return _typed_document_schema(doc_type, doc_type.load_schema())

    try:
        return _split_definitions(load_schema(ref))
    except FileNotFoundError:
        raise ValueError(f"unknown type ref: {ref!r}") from None
