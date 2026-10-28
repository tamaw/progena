"""User wall-clock context appended to model prompts."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, cast

from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import SystemMessage
from langchain_core.messages.content import TextContentBlock

from src.progena.cron import resolve_timezone, timezone_name


async def user_clock_context(state_manager: Any, *, has_shell: bool) -> str:
    """Return cache-stable user wall-clock guidance for one model call."""
    get_preferences = getattr(state_manager, "get_preferences_config", None)
    preferences = (
        await cast(Callable[[], Awaitable[dict[str, Any]]], get_preferences)()
        if callable(get_preferences)
        else {}
    )
    user_timezone = timezone_name(preferences.get("timezone"))
    # This changes only at the user's local midnight, preserving cached prompt
    # prefixes throughout their local day.
    today = datetime.now(UTC).astimezone(resolve_timezone(user_timezone))
    exact_time = (
        "For the exact current time, run `date -u` and convert it to the user's timezone."
        if has_shell
        else "For the exact current time, use the `current_time` tool and convert it to the user's timezone."
    )
    return (
        f"User timezone: {user_timezone}; today there is {today:%A %Y-%m-%d}. "
        f"Server clocks and shells use UTC. {exact_time}"
    )


def append_system_message_context(message: SystemMessage, suffix: str) -> SystemMessage:
    """Append ``suffix`` as the final system text block."""
    blocks = list(message.content_blocks)
    if blocks and blocks[-1]["type"] == "text":
        blocks[-1] = TextContentBlock(
            **{**blocks[-1], "text": blocks[-1]["text"] + "\n\n" + suffix}
        )
    else:
        blocks.append(TextContentBlock(type="text", text=suffix))
    return SystemMessage(content_blocks=blocks)


class ClockContextMiddleware(AgentMiddleware):
    """Append fresh date-level user clock context to every model call."""

    def __init__(self, state_manager: Any, *, has_shell: bool) -> None:
        super().__init__()
        self._state_manager = state_manager
        self._has_shell = has_shell

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        if request.system_message is not None:
            request = request.override(
                system_message=append_system_message_context(
                    request.system_message,
                    await user_clock_context(self._state_manager, has_shell=self._has_shell),
                )
            )
        return await handler(request)
