"""Explicit, paginated ACP result retrieval for model-facing evidence reads."""

from __future__ import annotations

import os
from typing import Literal
from urllib.parse import quote

import aiohttp
from pydantic import BaseModel, Field

from .protocol import BaseSystemTool, ToolContext, ToolResult

_ResultSource = Literal["output", "transcript", "input", "plan", "error"]


class ACPGetResultArgs(BaseModel):
    """Arguments for one task-bound ACP detail page."""

    task_id: str = Field(description="ACP task ID from acp_start or an ACP result reference.")
    turn: int | None = Field(
        default=None,
        ge=0,
        description="Specific historic turn; turn 0 reports honest queued-task availability.",
    )
    source: _ResultSource = Field(
        default="output",
        description="Evidence source: output, transcript, input, plan, or exact error.",
    )
    cursor: str | None = Field(
        default=None,
        max_length=64,
        description="Opaque cursor returned by the preceding acp_get_result page.",
    )
    limit: int = Field(
        default=8192,
        ge=1,
        le=8 * 1024,
        description="Maximum characters in this page. Retrieve later pages only when needed.",
    )


class ACPGetResultTool(BaseSystemTool):
    """Read exact ACP evidence only when an action needs it."""

    @property
    def args_model(self) -> type[BaseModel]:
        return ACPGetResultArgs

    @property
    def name(self) -> str:
        return "acp_get_result"

    @property
    def description(self) -> str:
        return (
            "Read one bounded page of exact ACP evidence for a task and optional turn. "
            "Use source='input' before answering a question or plan approval; use output for final "
            "evidence, error for an exact failure, and transcript only when raw native logs are "
            "required. Follow next_cursor only when the additional detail is necessary."
        )

    async def execute(self, context: ToolContext) -> ToolResult:
        args = context.tool_args if isinstance(context.tool_args, dict) else {}
        try:
            parsed = ACPGetResultArgs.model_validate(args)
        except ValueError as exc:
            return ToolResult.fail(str(exc))
        base_url = os.environ.get("PROGENA_BACKEND_URL", "http://127.0.0.1:8000").rstrip("/")
        if not base_url.startswith(("http://", "https://")):
            return ToolResult.fail("PROGENA_BACKEND_URL must be an HTTP URL")
        url = f"{base_url}/api/ui/acp/tasks/{quote(parsed.task_id, safe='')}/result"
        params = {
            "source": parsed.source,
            "limit": str(parsed.limit),
        }
        if parsed.turn is not None:
            params["turn"] = str(parsed.turn)
        if parsed.cursor is not None:
            params["cursor"] = parsed.cursor
        timeout = aiohttp.ClientTimeout(total=15)
        try:
            async with (
                aiohttp.ClientSession(timeout=timeout) as session,
                session.get(url, params=params) as response,
            ):
                payload = await response.json(content_type=None)
                if response.status >= 400:
                    detail = payload.get("detail") if isinstance(payload, dict) else None
                    return ToolResult.fail(
                        str(detail or f"ACP result read failed ({response.status})")
                    )
                if not isinstance(payload, dict):
                    return ToolResult.fail("ACP result reader returned an invalid response")
                return ToolResult.ok(payload)
        except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
            return ToolResult.fail(f"ACP result reader unavailable: {exc}")


__all__ = ["ACPGetResultTool"]
