"""
Structured Output Tool - generic schema-constrained JSON generation.

Given a JSON Schema (by name from ``data/schemas/`` or inline) plus an
instruction and optional input text, this returns a validated JSON value to the
agent. It does not touch the store - the result is returned into the agent's
context for further use (extraction, classification, parsing a request into
fields, or as the body for a subsequent native ``write``/``edit`` file-tool
call against a mounted state document).
"""

import json
import logging
from typing import Any

from pydantic import BaseModel, Field

from ..schemas import load_schema
from ..structured import generate_structured
from .protocol import BaseSystemTool, ToolContext, ToolResult

logger = logging.getLogger(__name__)


class StructuredOutputArgs(BaseModel):
    """Arguments for the structured_output tool."""

    instruction: str = Field(
        description=(
            "What to produce or extract, described in natural language "
            "(e.g. 'extract the action items as a list')."
        )
    )
    schema_file: str | None = Field(
        default=None,
        description=(
            "Name or filename of a JSON Schema under data/schemas/ to conform to. "
            "Provide this OR json_schema, not both."
        ),
    )
    json_schema: dict[str, Any] | None = Field(
        default=None,
        description=(
            "An inline JSON Schema (object) to conform to, for ad-hoc shapes with "
            "no persisted contract. Provide this OR schema_file, not both."
        ),
    )
    input: str | None = Field(
        default=None,
        description="Optional source text/content to extract or transform.",
    )


class StructuredOutputTool(BaseSystemTool):
    """Generate a validated JSON value from a schema without persisting it."""

    @property
    def args_model(self) -> type[BaseModel]:
        return StructuredOutputArgs

    @property
    def name(self) -> str:
        return "structured_output"

    @property
    def description(self) -> str:
        return (
            "Generate a validated JSON value that conforms to a JSON Schema and "
            "return it (does NOT save anything). Use for extraction, "
            "classification, parsing a request into fields, or building a payload "
            "for another tool.\n\n"
            "Provide:\n"
            "- instruction: what to produce or extract\n"
            "- schema_file: a schema name under data/schemas/, OR\n"
            "- json_schema: an inline JSON Schema object\n"
            "- input: (optional) source text to extract/transform\n\n"
            "To persist the result, write it with the native write/edit file tools."
        )

    async def execute(self, context: ToolContext) -> ToolResult:
        if context.llm_client is None:
            return ToolResult.fail("llm_client unavailable; cannot run structured output")

        args = context.tool_args if isinstance(context.tool_args, dict) else {}
        instruction = str(args.get("instruction") or context.user_input or "").strip()
        schema_file = str(args.get("schema_file") or "").strip()
        inline_schema = args.get("json_schema")
        source_input = args.get("input")

        if not instruction:
            return ToolResult.fail("Missing 'instruction' describing what to produce")

        has_file = bool(schema_file)
        has_inline = isinstance(inline_schema, dict) and bool(inline_schema)
        if has_file == has_inline:
            return ToolResult.fail("Provide exactly one of 'schema_file' or 'json_schema'")

        if has_file:
            try:
                schema = load_schema(schema_file)
            except FileNotFoundError as exc:
                return ToolResult.fail(str(exc))
            except (ValueError, json.JSONDecodeError) as exc:
                return ToolResult.fail(f"Invalid schema '{schema_file}': {exc}")
            schema_label = schema_file
        else:
            schema = inline_schema
            schema_label = "inline"

        value, error = await generate_structured(
            context.llm_client,
            json_schema=schema,
            instruction=instruction,
            context=source_input,
            context_label="Input",
            schema_name="structured_output",
        )
        if error is not None:
            return ToolResult.fail(f"Structured output failed: {error}")

        return ToolResult.ok(
            {
                "success": True,
                "schema": schema_label,
                "validated": True,
                "value": value,
            }
        )
