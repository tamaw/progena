"""Keep every tool result paired with the model tool call that requested it.

Providers reject a request whose history contains a tool result with no earlier
tool call of the same id (OpenAI Responses: ``No tool call found for function
call output``). One such message poisons every later turn on its thread, so the
model request drops unpaired results instead of failing. The checkpoint itself
is left untouched.
"""

import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, ModelRequest
from langchain_core.messages import AIMessage, AnyMessage, ToolMessage

logger = logging.getLogger(__name__)

_LOGGED_ID_LIMIT = 5


def _drop_unpaired_tool_results(
    messages: Sequence[AnyMessage],
) -> tuple[list[AnyMessage], list[str]]:
    requested: set[str] = set()
    kept: list[AnyMessage] = []
    dropped: list[str] = []
    for message in messages:
        if isinstance(message, AIMessage):
            for call in (*message.tool_calls, *message.invalid_tool_calls):
                if call["id"]:
                    requested.add(call["id"])
        elif isinstance(message, ToolMessage) and message.tool_call_id not in requested:
            dropped.append(message.tool_call_id)
            continue
        kept.append(message)
    return kept, dropped


class ToolResultPairingMiddleware(AgentMiddleware):
    """Drop tool results that no earlier model tool call requested."""

    def _paired_request(self, request: ModelRequest[Any]) -> ModelRequest[Any]:
        messages, dropped = _drop_unpaired_tool_results(request.messages)
        if not dropped:
            return request
        shown = ", ".join(dropped[:_LOGGED_ID_LIMIT])
        more = len(dropped) - _LOGGED_ID_LIMIT
        logger.warning(
            "Dropped %d unpaired tool result(s) from the model request: %s%s",
            len(dropped),
            shown,
            f" (+{more} more)" if more > 0 else "",
        )
        return request.override(messages=messages)

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Any],
    ) -> Any:
        return handler(self._paired_request(request))

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[Any]],
    ) -> Any:
        return await handler(self._paired_request(request))
