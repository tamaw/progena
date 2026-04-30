"""Responses API schema helpers."""

from typing import Any


def close_object_schemas(node: Any) -> None:
    """Recursively enforce strict object schemas Azure Responses accepts."""
    if isinstance(node, dict):
        if "$ref" in node:
            ref = node["$ref"]
            node.clear()
            node["$ref"] = ref
            return
        if node.get("type") == "object":
            node["additionalProperties"] = False
            properties = node.get("properties")
            if isinstance(properties, dict):
                node["required"] = list(properties.keys())
        for value in node.values():
            close_object_schemas(value)
        return
    if isinstance(node, list):
        for item in node:
            close_object_schemas(item)


def build_json_schema_format(response_model: Any, schema_name: str) -> dict[str, Any]:
    """Build response_format dict for structured output using a Pydantic model."""
    schema = response_model.model_json_schema()
    close_object_schemas(schema)

    return {
        "type": "json_schema",
        "json_schema": {
            "name": schema_name,
            "schema": schema,
            "strict": True,
        },
    }


def to_azure_text_format(response_format: dict[str, Any]) -> dict[str, Any]:
    """Convert OpenAI-style response_format to Azure Responses text.format."""
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
