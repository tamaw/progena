"""Reusable schema-constrained structured-output engine.

The core primitive is "schema + instruction -> validated JSON", used by the
generic ``structured_output`` tool: a skill resolves ids/timestamps, calls this
to produce a validated JSON value, then persists it itself with the native
``write``/``edit`` file tools.

The provider requires an object-root schema, so top-level array schemas are
transparently wrapped in ``{"items": [...]}`` for the request and unwrapped
before validation/return. Conformance is enforced locally with ``jsonschema``
(non-strict provider mode), which lets schemas use features strict mode forbids
(arbitrary-key maps, optional/nullable fields, unions).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

ARRAY_WRAP_KEY = "items"

_SCHEMA_NAME_PATTERN = re.compile(r"[^A-Za-z0-9_]+")

_DEFAULT_SYSTEM_PROMPT = (
    "You produce a single JSON value that conforms exactly to the provided JSON "
    "Schema. Do not include any text outside the JSON. Do not invent fields that "
    "are not in the schema."
)


def schema_safe_name(name: str) -> str:
    """Derive a provider-safe schema name (alphanumeric + underscore)."""
    cleaned = _SCHEMA_NAME_PATTERN.sub("_", name).strip("_")
    return cleaned or "structured_output"


def wrap_array_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Wrap a top-level array schema in an object so the provider accepts it."""
    return {
        "type": "object",
        "properties": {ARRAY_WRAP_KEY: schema},
        "required": [ARRAY_WRAP_KEY],
        "additionalProperties": False,
    }


def validate_against_schema(value: Any, schema: dict[str, Any]) -> str | None:
    """Validate ``value`` against ``schema``; return an error message or None.

    Validation is skipped (returns None) if ``jsonschema`` is unavailable.
    """
    try:
        import jsonschema
    except ImportError:  # pragma: no cover - jsonschema is a project dependency
        logger.debug("jsonschema unavailable; skipping structured-output validation")
        return None

    try:
        jsonschema.validate(value, schema)
    except jsonschema.ValidationError as exc:
        return str(exc.message)
    except jsonschema.SchemaError as exc:  # pragma: no cover - authoring error
        return f"invalid schema: {exc.message}"
    return None


def _build_prompt(
    request_schema: dict[str, Any],
    instruction: str,
    context: Any,
    context_label: str,
) -> str:
    parts = [f"JSON Schema:\n{json.dumps(request_schema, indent=2)}"]
    if context is not None:
        parts.append(f"{context_label}:\n{json.dumps(context, indent=2, default=str)}")
    parts.append(f"Task:\n{instruction}")
    parts.append("Return the complete result as JSON conforming to the schema.")
    return "\n\n".join(parts)


async def generate_structured(
    llm_client: Any,
    *,
    json_schema: dict[str, Any],
    instruction: str,
    context: Any = None,
    context_label: str = "Current value",
    schema_name: str = "structured_output",
    system_prompt: str | None = None,
) -> tuple[Any, str | None]:
    """Run a schema-constrained structured-output call.

    Builds the prompt, handles object-root wrapping for array schemas, calls the
    provider in non-strict mode, unwraps array results, and validates against the
    original schema.

    Returns ``(value, None)`` on success or ``(None, error_message)`` on a
    provider failure, a missing array wrapper, or a validation failure.
    """
    is_array_root = json_schema.get("type") == "array"
    request_schema = wrap_array_schema(json_schema) if is_array_root else json_schema

    # Only wrap context that already matches the wrapped schema's shape (a
    # "current array value" being edited). Free-form context like extraction
    # source text is never a list here - passing it through unwrapped keeps it
    # in the prompt instead of silently discarding it as an empty list.
    prompt_context: Any = context
    if is_array_root and isinstance(context, list):
        prompt_context = {ARRAY_WRAP_KEY: context}

    prompt = _build_prompt(request_schema, instruction, prompt_context, context_label)

    try:
        response = await llm_client.chat_structured_json(
            prompt=prompt,
            json_schema=request_schema,
            schema_name=schema_safe_name(schema_name),
            system_prompt=system_prompt or _DEFAULT_SYSTEM_PROMPT,
            strict=False,
        )
    except json.JSONDecodeError as exc:
        return None, f"Model did not return valid JSON: {exc}"
    except Exception as exc:  # pragma: no cover - upstream/provider failures
        logger.error("structured-output call failed: %s", exc, exc_info=True)
        return None, f"Structured output call failed: {exc}"

    if is_array_root:
        if not isinstance(response, dict) or ARRAY_WRAP_KEY not in response:
            return None, f"Model output for array schema missing '{ARRAY_WRAP_KEY}' wrapper"
        value = response[ARRAY_WRAP_KEY]
    else:
        value = response

    validation_error = validate_against_schema(value, json_schema)
    if validation_error is not None:
        return None, f"Generated output does not conform to schema: {validation_error}"

    return value, None
