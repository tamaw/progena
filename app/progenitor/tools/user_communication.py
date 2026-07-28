"""
User Communication Tools for Progenitor.

Provides tools for the agent to communicate with the user through SSE:
- say_to_user: Send a message to the user
- ask_user: Ask the user a question and await response
"""

from collections.abc import Callable, Coroutine
from typing import Any, cast

from pydantic import BaseModel, Field

from .protocol import BaseSystemTool, ToolContext, ToolResult

# Type alias for event pusher callback
EventPusher = Callable[[str, str, dict[str, Any] | None], Coroutine[Any, Any, None]]


class SayToUserArgs(BaseModel):
    """Structured arguments for sending a one-way user message."""

    message: str = Field(description="The message to send to the user")
    context: str | None = Field(
        default=None,
        description="Optional context about what triggered this message",
    )


class AskUserArgs(BaseModel):
    """Structured arguments for sending a one-way user question."""

    question: str = Field(description="The question to ask the user")
    context: str | None = Field(
        default=None,
        description="Optional context about why this question is being asked",
    )
    options: list[str] | None = Field(
        default=None,
        description="Optional list of suggested answers/options",
    )


class NotifyUserArgs(BaseModel):
    """Structured arguments for backend-managed user notifications."""

    title: str = Field(description="Short notification title")
    body: str = Field(description="Notification body/details")
    level: str = Field(
        default="info",
        description="Notification level: info, warning, error, success",
    )


class SayToUserTool(BaseSystemTool):
    """Send a one-way agent_message event via SSE."""

    @property
    def args_model(self) -> type[BaseModel]:
        return SayToUserArgs

    @property
    def name(self) -> str:
        return "say_to_user"

    @property
    def description(self) -> str:
        return "Send a one-way message to the user via SSE (agent_message event)"

    async def execute(self, context: ToolContext) -> ToolResult:
        args = context.tool_args if isinstance(context.tool_args, dict) else {}
        message = str(args.get("message") or context.user_input or "").strip()
        if not message:
            return ToolResult.fail("Missing 'message'")

        push = getattr(context.llm_client, "_push_event", None)
        if not callable(push):
            return ToolResult.fail("LLM client has no SSE event pusher configured")

        await cast(EventPusher, push)(
            "agent_message",
            message,
            {"tool": "say_to_user"},
        )
        return ToolResult.ok({"status": "sent", "message": message})


class AskUserTool(BaseSystemTool):
    """Send a one-way agent_question event via SSE."""

    @property
    def args_model(self) -> type[BaseModel]:
        return AskUserArgs

    @property
    def name(self) -> str:
        return "ask_user"

    @property
    def description(self) -> str:
        return "Ask the user a question via SSE (agent_question event; one-way notification)"

    async def execute(self, context: ToolContext) -> ToolResult:
        args = context.tool_args if isinstance(context.tool_args, dict) else {}
        question = str(args.get("question") or context.user_input or "").strip()
        if not question:
            return ToolResult.fail("Missing 'question'")

        options = args.get("options")
        options_out = options if isinstance(options, list) else None

        push = getattr(context.llm_client, "_push_event", None)
        if not callable(push):
            return ToolResult.fail("LLM client has no SSE event pusher configured")

        await cast(EventPusher, push)(
            "agent_question",
            question,
            {
                "tool": "ask_user",
                "options": options_out,
                "requires_response": True,
            },
        )
        return ToolResult.ok({"status": "sent", "question": question, "options": options_out})


class NotifyUserTool(BaseSystemTool):
    """Request a backend-managed alert via SSE."""

    @property
    def args_model(self) -> type[BaseModel]:
        return NotifyUserArgs

    @property
    def name(self) -> str:
        return "notify_user"

    @property
    def description(self) -> str:
        return "Create a backend-managed alert and let the backend drive user-facing delivery"

    async def execute(self, context: ToolContext) -> ToolResult:
        args = context.tool_args if isinstance(context.tool_args, dict) else {}
        title = str(args.get("title") or "").strip()
        body = str(args.get("body") or context.user_input or "").strip()
        level = str(args.get("level") or "info").strip() or "info"

        if not title:
            return ToolResult.fail("Missing 'title'")
        if not body:
            return ToolResult.fail("Missing 'body'")

        push = getattr(context.llm_client, "_push_event", None)
        if not callable(push):
            return ToolResult.fail("LLM client has no SSE event pusher configured")

        await cast(EventPusher, push)(
            "alert_request",
            body,
            {"tool": "notify_user", "title": title, "level": level},
        )
        return ToolResult.ok({"status": "sent", "title": title, "level": level})
