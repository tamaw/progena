"""Use the current stored system instruction on every model call.

The graph is built once with the system instruction it booted with. Runtime
instructions are edited on the Instructions page while the runtime keeps
running, so this middleware swaps that boot text for the stored text on each
model call: an edit applies to the next model call without a restart.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import SystemMessage
from langchain_core.messages.content import TextContentBlock

from src.progena.agent.clock_context import append_system_message_context, user_clock_context
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
        return (await render_runtime_instruction(self._state_manager, content)).strip()

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        current = await self._current_text()
        if request.system_message is not None:
            system_message = request.system_message
            if current != self._boot_text:
                system_message = _replace_leading_text(system_message, self._boot_text, current)
            if system_message is not None:
                request = request.override(
                    system_message=append_system_message_context(
                        system_message,
                        await user_clock_context(
                            self._state_manager,
                            has_shell=False,
                            has_current_time=any(
                                getattr(tool, "name", "") == "current_time"
                                for tool in request.tools
                            ),
                        ),
                    )
                )
        return await handler(request)
