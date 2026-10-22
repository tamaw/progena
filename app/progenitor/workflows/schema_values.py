"""Pure schema traversal and unpack-node schema construction."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any


def schema_at_path(ports: Mapping[str, Any], path: str) -> dict[str, Any] | None:
    """Resolve a dotted path against port schemas' nested ``properties``.

    ``None`` when a segment is not declared; an object schema that declares no
    ``properties`` at all is open, so any deeper path resolves to ``{}``.
    """

    schema: Any = {"properties": ports}
    for part in path.split("."):
        if not isinstance(schema, Mapping):
            return None
        properties = schema.get("properties")
        if properties is None and _admits_object(schema):
            return {}
        if not isinstance(properties, Mapping) or part not in properties:
            return None
        schema = properties[part]
    return dict(schema) if isinstance(schema, Mapping) else {}


def _admits_object(schema: Mapping[str, Any]) -> bool:
    """Whether the schema's ``type`` is ``object`` or a union including it."""

    schema_type = schema.get("type")
    return schema_type == "object" or (isinstance(schema_type, list) and "object" in schema_type)


def unpackable_properties(schema: Mapping[str, Any] | None) -> dict[str, Any]:
    """Properties of an object port schema an Unpack node can expose (empty if none).

    A nullable object (``["object", "null"]``) exposes its fields too.
    """

    if not isinstance(schema, Mapping) or not _admits_object(schema):
        return {}
    properties = schema.get("properties")
    return dict(properties) if isinstance(properties, Mapping) else {}


def _resolve_local_ref(
    schema: Mapping[str, Any] | None, definitions: Mapping[str, Any]
) -> Mapping[str, Any] | None:
    """Follow ``#/$defs/...`` / ``#/definitions/...`` references to their target schema."""

    seen: set[str] = set()
    while isinstance(schema, Mapping) and isinstance(schema.get("$ref"), str):
        ref = schema["$ref"]
        container, _, name = ref.removeprefix("#/").partition("/")
        group = definitions.get(container) if ref.startswith("#/") else None
        target = group.get(name) if isinstance(group, Mapping) and "/" not in name else None
        if ref in seen or not isinstance(target, Mapping):
            return schema
        seen.add(ref)
        schema = target
    return schema


def _unpack_input_schema(
    source: Mapping[str, Any] | None, definitions: Mapping[str, Any]
) -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["value"],
        "properties": {"value": copy.deepcopy(dict(source)) if source else {"type": "object"}},
        **copy.deepcopy(dict(definitions)),
    }


def _unpack_output_schema(
    source: Mapping[str, Any] | None, definitions: Mapping[str, Any]
) -> dict[str, Any]:
    source = _resolve_local_ref(source, definitions)
    required = source.get("required") if source else None
    return {
        "type": "object",
        "properties": copy.deepcopy(unpackable_properties(source)),
        "required": list(required) if isinstance(required, list) else [],
        **copy.deepcopy(dict(definitions)),
    }
