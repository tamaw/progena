"""Use the current stored system instruction on every model call.

The graph is built once with the system instruction it booted with. Runtime
instructions are edited on the Instructions page while the runtime keeps
running, so this middleware swaps that boot text for the stored text on each
model call: an edit applies to the next model call without a restart.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import SystemMessage
from langchain_core.messages.content import TextContentBlock

from src.progena.cron import resolve_timezone, timezone_name
from src.progena.instructions import render_runtime_instruction


def _replace_leading_text(message: SystemMessage, old: str, new: str) -> SystemMessage | None:
    blocks = list(message.content_blocks)
    first = blocks[0] if blocks else None
    if first is None or first["type"] != "text" or not first["text"].startswith(old):
        return None
    blocks[0] = TextContentBlock(**{**first, "text": new + first["text"][len(old) :]})
    return SystemMessage(content_blocks=blocks)


class LiveInstructionMiddleware(AgentMiddleware):
    """Replace the boot-time system instruction with its current stored body."""

    def __init__(self, state_manager: Any, path: str, boot_text: str) -> None:
        super().__init__()
        self._state_manager = state_manager
        self._path = path
        self._boot_text = boot_text

    async def _current_text(self) -> str:
        content = await self._state_manager.get_content_document("instruction", self._path)
        if content is None:
            raise RuntimeError(f"Instruction '{self._path}' is missing from the store")
        preferences = await self._state_manager.get_preferences_config()
        user_timezone = timezone_name(preferences.get("timezone"))
        # Date-level only: this text leads the system prompt, so a value that
        # changed every minute would invalidate the provider's prompt cache on
        # most calls. It changes once a day at the user's local midnight.
        today = datetime.now(UTC).astimezone(resolve_timezone(user_timezone))
        return (
            f"{(await render_runtime_instruction(self._state_manager, content)).strip()}\n\n"
            f"User timezone: {user_timezone}; today there is {today:%A %Y-%m-%d}. "
            "Server clocks and shells use UTC: for the exact current time run "
            "`date -u` and convert it to the user's timezone."
        )

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        current = await self._current_text()
        if current != self._boot_text and request.system_message is not None:
            replaced = _replace_leading_text(request.system_message, self._boot_text, current)
            if replaced is not None:
                request = request.override(system_message=replaced)
        return await handler(request)
