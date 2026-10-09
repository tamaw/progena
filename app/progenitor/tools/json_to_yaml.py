"""
JSON to YAML Tool - render a structured_output value as YAML frontmatter.

Kanban cards, inbox messages, and other .mdc-style virtual files are YAML
frontmatter plus a markdown body (see e.g. managing-tasks, managing-inbox).
Models write raw YAML by hand unreliably (indentation, quoting, escaping), but
they generate schema-conformant JSON well via ``structured_output``. This tool
takes that JSON value and mechanically renders it into the frontmatter+body
shape, so the only thing left to hand-author is the schema-validated content.
"""

import logging
from typing import Any

import yaml
from pydantic import BaseModel, Field

from .protocol import BaseSystemTool, ToolContext, ToolResult

logger = logging.getLogger(__name__)


class JsonToYamlArgs(BaseModel):
    """Arguments for the json_to_yaml tool."""

    data: dict[str, Any] = Field(description="JSON object to render as YAML.")
    body_field: str | None = Field(
        default=None,
        description=(
            "If set, this key is pulled out of 'data' and appended below a "
            "closing '---' as the markdown body -- the frontmatter+body "
            "convention used by kanban cards ('description') and inbox "
            "messages ('body'). Leave unset to render 'data' as a plain YAML "
            "document with no frontmatter delimiters."
        ),
    )


class JsonToYamlTool(BaseSystemTool):
    """Render a JSON object as YAML, optionally as frontmatter + markdown body."""

    @property
    def args_model(self) -> type[BaseModel]:
        return JsonToYamlArgs

    @property
    def name(self) -> str:
        return "json_to_yaml"

    @property
    def description(self) -> str:
        return (
            "Render a JSON object (e.g. from structured_output) as YAML. Use "
            "this instead of hand-writing YAML frontmatter -- it guarantees "
            "valid syntax (quoting, escaping, indentation) for whatever field "
            "names and values the JSON already has.\n\n"
            "Provide:\n"
            "- data: the JSON object to render\n"
            "- body_field: (optional) a key in 'data' whose value becomes the "
            "markdown body below a closing '---', matching the frontmatter+body "
            "shape of files like /tasks/<id>.md (body_field='description') "
            "or /inbox/<id>.md (body_field='body'). Omit for a plain YAML dump.\n\n"
            "Returns 'content': the rendered text, ready to pass straight to "
            "write_file/edit_file."
        )

    async def execute(self, context: ToolContext) -> ToolResult:
        args = context.tool_args if isinstance(context.tool_args, dict) else {}
        data = args.get("data")
        if not isinstance(data, dict):
            return ToolResult.fail("'data' must be a JSON object")

        body_field = args.get("body_field")
        fields = dict(data)
        body = ""
        if isinstance(body_field, str) and body_field:
            body = str(fields.pop(body_field, "") or "").strip("\n")

        try:
            frontmatter = yaml.safe_dump(
                fields, sort_keys=True, default_flow_style=False, allow_unicode=True
            ).strip()
        except yaml.YAMLError as e:
            return ToolResult.fail(f"Failed to render YAML: {e}")

        if body_field is None:
            content = frontmatter + "\n"
        elif body:
            content = f"---\n{frontmatter}\n---\n\n{body}\n"
        else:
            content = f"---\n{frontmatter}\n---\n"

        return ToolResult.ok({"success": True, "content": content})
