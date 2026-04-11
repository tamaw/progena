"""
User Communication Tools for Progenitor.

Provides tools for the agent to communicate with the user through IPC:
- say_to_user: Send a message to the user
- ask_user: Ask the user a question and await response
"""

import logging
from collections.abc import Callable, Coroutine
from typing import Any, cast

from .protocol import BaseSystemTool, ToolContext, ToolResult

logger = logging.getLogger(__name__)


# Type alias for event pusher callback
EventPusher = Callable[[str, str, dict[str, Any] | None], Coroutine[Any, Any, None]]


class UserCommunicationTools:
    """Tools for agent-to-user communication via IPC events.

    These tools allow the agent to:
    - Send messages to the user (say_to_user)
    - Ask questions and await responses (ask_user)

    The tools work by pushing events through the IPC channel, which are
    then received by connected clients (e.g., the frontend).
    """

    def __init__(self, event_pusher: EventPusher, run_id: str | None = None):
        """Initialize user communication tools.

        Args:
            event_pusher: Async callback to push events to clients.
                         Signature: (event_type, content, metadata) -> None
            run_id: Current run ID for context.
        """
        self._push_event = event_pusher
        self._run_id = run_id

    async def say_to_user(
        self,
        message: str,
        context: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send a message to the user.

        This pushes a message event through IPC that the frontend can display
        to the user. Use this when the agent needs to communicate information,
        status updates, or results during scheduled/background operations.

        Args:
            message: The message to send to the user.
            context: Optional context about what triggered this message.
            metadata: Optional additional metadata to include with the event.

        Returns:
            Result dict with status and message_id.
        """
        logger.info("say_to_user: %s", message[:100] if len(message) > 100 else message)

        event_metadata = {
            "run_id": self._run_id,
            "tool": "say_to_user",
            "context": context,
            **(metadata or {}),
        }

        await self._push_event(
            "agent_message",
            message,
            event_metadata,
        )

        return {
            "status": "sent",
            "message": message,
            "tool": "say_to_user",
        }

    async def ask_user(
        self,
        question: str,
        context: str | None = None,
        options: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Ask the user a question.

        This pushes a question event through IPC that the frontend can display.
        The question will be shown to the user, but this is a one-way notification -
        the agent cannot wait for user input in background tasks.

        For interactive Q&A, the question will be queued for when the user
        next interacts with the system.

        Args:
            question: The question to ask the user.
            context: Optional context about why this question is being asked.
            options: Optional list of suggested answers/options.
            metadata: Optional additional metadata to include with the event.

        Returns:
            Result dict with status and question_id.
        """
        logger.info("ask_user: %s", question[:100] if len(question) > 100 else question)

        event_metadata = {
            "run_id": self._run_id,
            "tool": "ask_user",
            "context": context,
            "options": options,
            "requires_response": True,
            **(metadata or {}),
        }

        await self._push_event(
            "agent_question",
            question,
            event_metadata,
        )

        return {
            "status": "sent",
            "question": question,
            "tool": "ask_user",
            "options": options,
            "note": "Question sent to user. Response will be available in next interaction.",
        }

    async def notify_user(
        self,
        title: str,
        body: str,
        level: str = "info",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Request a backend-managed alert for the user.

        Args:
            title: Short notification title.
            body: Notification body/details.
            level: Notification level - 'info', 'warning', 'error', 'success'.
            metadata: Optional additional metadata.

        Returns:
            Result dict with status.
        """
        logger.info("notify_user [%s]: %s", level, title)

        event_metadata = {
            "run_id": self._run_id,
            "tool": "notify_user",
            "title": title,
            "level": level,
            **(metadata or {}),
        }

        await self._push_event("alert_request", body, event_metadata)

        return {
            "status": "sent",
            "title": title,
            "level": level,
            "tool": "notify_user",
        }


# Tool definitions for registration
USER_COMMUNICATION_TOOLS = [
    {
        "name": "say_to_user",
        "description": "Send a message to the user. Use this to communicate information, updates, or results. Aliases: 'say', 'tell', 'to user', 'message user'.",
        "type": "system_tool",
        "capabilities": ["messaging", "say", "tell"],
        "parameters": {
            "message": {
                "type": "string",
                "description": "The message to send to the user",
                "required": True,
            },
            "context": {
                "type": "string",
                "description": "Optional context about what triggered this message",
                "required": False,
            },
        },
    },
    {
        "name": "ask_user",
        "description": "Ask the user a question. Use this when you need clarification or input from the user. Aliases: 'ask', 'question', 'inquire'.",
        "type": "system_tool",
        "capabilities": ["questioning", "ask", "inquire"],
        "parameters": {
            "question": {
                "type": "string",
                "description": "The question to ask the user",
                "required": True,
            },
            "context": {
                "type": "string",
                "description": "Optional context about why this question is being asked",
                "required": False,
            },
            "options": {
                "type": "array",
                "description": "Optional list of suggested answers/options",
                "required": False,
            },
        },
    },
    {
        "name": "notify_user",
        "description": "Send a notification to the user with a title and body. Use for important alerts or status updates.",
        "type": "system_tool",
        "capabilities": ["notifications", "alerts"],
        "parameters": {
            "title": {
                "type": "string",
                "description": "Short notification title",
                "required": True,
            },
            "body": {
                "type": "string",
                "description": "Notification body/details",
                "required": True,
            },
            "level": {
                "type": "string",
                "description": "Notification level: info, warning, error, success",
                "required": False,
                "default": "info",
            },
        },
    },
]


class SayToUserTool(BaseSystemTool):
    """Send a one-way agent_message event via IPC."""

    @property
    def name(self) -> str:
        return "say_to_user"

    @property
    def description(self) -> str:
        return "Send a one-way message to the user via IPC (agent_message event)"

    async def execute(self, context: ToolContext) -> ToolResult:
        args = context.tool_args if isinstance(context.tool_args, dict) else {}
        message = str(args.get("message") or context.user_input or "").strip()
        if not message:
            return ToolResult.fail("Missing 'message'")

        push = getattr(context.llm_client, "_push_event", None)
        if not callable(push):
            return ToolResult.fail("LLM client has no IPC event pusher configured")

        await cast(EventPusher, push)(
            "agent_message",
            message,
            {"tool": "say_to_user"},
        )
        return ToolResult.ok({"status": "sent", "message": message})


class AskUserTool(BaseSystemTool):
    """Send a one-way agent_question event via IPC."""

    @property
    def name(self) -> str:
        return "ask_user"

    @property
    def description(self) -> str:
        return "Ask the user a question via IPC (agent_question event; one-way notification)"

    async def execute(self, context: ToolContext) -> ToolResult:
        args = context.tool_args if isinstance(context.tool_args, dict) else {}
        question = str(args.get("question") or context.user_input or "").strip()
        if not question:
            return ToolResult.fail("Missing 'question'")

        options = args.get("options")
        options_out = options if isinstance(options, list) else None

        push = getattr(context.llm_client, "_push_event", None)
        if not callable(push):
            return ToolResult.fail("LLM client has no IPC event pusher configured")

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
    """Request a backend-managed alert via IPC."""

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
            return ToolResult.fail("LLM client has no IPC event pusher configured")

        await cast(EventPusher, push)(
            "alert_request",
            body,
            {"tool": "notify_user", "title": title, "level": level},
        )
        return ToolResult.ok({"status": "sent", "title": title, "level": level})
