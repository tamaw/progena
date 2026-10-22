"""Pure schema helpers for auxiliary model calls."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


def _close_object_schemas(node: Any) -> None:
    """Recursively enforce strict object schemas Azure Responses accepts."""
    if isinstance(node, dict):
        if "$ref" in node:
            ref = node["$ref"]
            node.clear()
            node["$ref"] = ref
            return
        if node.get("type") == "object":
            # A dict-valued additionalProperties is itself a schema (an
            # arbitrary-key map's value shape) and must be preserved and
            # recursed into, not clobbered with False - overwriting it here
            # would leave the map unable to hold any properties at all.
            if not isinstance(node.get("additionalProperties"), dict):
                node["additionalProperties"] = False
            properties = node.get("properties")
            if isinstance(properties, dict):
                node["required"] = list(properties.keys())
        for value in node.values():
            _close_object_schemas(value)
        return
    if isinstance(node, list):
        for item in node:
            _close_object_schemas(item)


def _build_json_schema_format(response_model: Any, schema_name: str) -> dict[str, Any]:
    """Build response_format dict for structured output using Pydantic model.

    Args:
        response_model: Pydantic BaseModel class to generate schema from
        schema_name: Name for the JSON schema

    Returns:
        Dictionary in format: {"type": "json_schema", "json_schema": {...}}
    """
    schema = response_model.model_json_schema()
    _close_object_schemas(schema)

    return {
        "type": "json_schema",
        "json_schema": {
            "name": schema_name,
            "schema": schema,
            "strict": True,
        },
    }


def _build_json_schema_format_from_dict(
    json_schema: dict[str, Any], schema_name: str, *, strict: bool = True
) -> dict[str, Any]:
    """Build a response_format dict from a raw JSON Schema document.

    Companion to :func:`_build_json_schema_format` for callers that load a
    schema from a file at runtime instead of a Pydantic model. The schema is
    deep-copied so the caller's dict is left untouched.

    When ``strict`` is True the schema is rewritten into the closed-object form
    the provider's strict mode requires. When False the schema is passed through
    as-is.

    ``strict=False`` is still required for schemas with arbitrary-key maps
    (dict-valued ``additionalProperties``): the provider's real strict mode
    rejects anything but a literal ``additionalProperties: false`` on every
    object, so such a schema is never strict-mode-compatible regardless of
    this function. ``_close_object_schemas`` recursing into a dict-valued
    ``additionalProperties`` instead of clobbering it with ``False`` only
    keeps the *closed* schema self-consistent (no longer claiming
    ``minProperties: 1`` while simultaneously forbidding every property) for
    callers that pass ``strict=True`` anyway; it does not make the provider
    accept the map.

    The provider requires an explicit top-level ``"type": "object"`` even
    outside strict mode. Plain JSON Schema treats a root schema with no
    ``type`` key (just ``properties``/``required``) as implicitly
    object-shaped, so default it in rather than rejecting otherwise-valid
    schemas the provider is stricter than the spec about.
    """
    schema = deepcopy(json_schema)
    schema.setdefault("type", "object")
    if strict:
        _close_object_schemas(schema)

    return {
        "type": "json_schema",
        "json_schema": {
            "name": schema_name,
            "schema": schema,
            "strict": strict,
        },
    }


def to_azure_text_format(response_format: dict[str, Any]) -> dict[str, Any]:
    if response_format.get("type") != "json_schema":
        return response_format

    json_schema = response_format.get("json_schema")
    if not isinstance(json_schema, dict):
        return response_format

    return {
        "type": "json_schema",
        "name": json_schema.get("name") or "structured_output",
        "schema": json_schema.get("schema") or {},
        "strict": bool(json_schema.get("strict", True)),
    }
