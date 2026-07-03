"""
Generate ID Tool - deterministic id generator for state-document entries.

Skills that build new entries via ``structured_output`` (e.g. adding a
schedule entry) need a stable, unique id. Models are poor at producing
high-entropy unique ids, so this returns one deterministically: ``<prefix>_<hex>``.
The agent carries the returned id into its ``structured_output`` instruction and
the file path it then writes.
"""

import logging
import re
import uuid

from pydantic import BaseModel, Field

from .protocol import BaseSystemTool, ToolContext, ToolResult

logger = logging.getLogger(__name__)

_PREFIX_PATTERN = re.compile(r"[^a-z0-9]+")


class GenerateIdArgs(BaseModel):
    """Arguments for the generate_id tool."""

    prefix: str = Field(
        default="id",
        description=(
            "Short prefix for the id (e.g. 'sched', 'cron', 'card'). Lowercased; "
            "non-alphanumeric characters are stripped."
        ),
    )
    count: int = Field(
        default=1,
        ge=1,
        le=50,
        description="How many ids to generate (1-50).",
    )


def _normalize_prefix(prefix: str) -> str:
    cleaned = _PREFIX_PATTERN.sub("_", prefix.strip().lower()).strip("_")
    return cleaned or "id"


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class GenerateIdTool(BaseSystemTool):
    """Generate one or more unique ids for new state-document entries."""

    @property
    def args_model(self) -> type[BaseModel]:
        return GenerateIdArgs

    @property
    def name(self) -> str:
        return "generate_id"

    @property
    def description(self) -> str:
        return (
            "Generate unique id(s) of the form '<prefix>_<hex>' for new entries "
            "you are about to create. Use this instead of inventing ids yourself. "
            "Provide:\n"
            "- prefix: short id prefix (e.g. 'sched' for schedules, 'card' for "
            "kanban cards)\n"
            "- count: how many ids to generate (default 1)\n\n"
            "Carry the returned id(s) into your structured_output instruction and "
            "the file path you write."
        )

    async def execute(self, context: ToolContext) -> ToolResult:
        args = context.tool_args if isinstance(context.tool_args, dict) else {}
        prefix = _normalize_prefix(str(args.get("prefix") or "id"))
        try:
            count = int(args.get("count") or 1)
        except (TypeError, ValueError):
            count = 1
        count = max(1, min(count, 50))

        ids = [_new_id(prefix) for _ in range(count)]
        return ToolResult.ok({"success": True, "prefix": prefix, "ids": ids, "id": ids[0]})
