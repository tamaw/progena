"""Final node — pass through the dispatched subagent's result text."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from ..state import RouterState

logger = logging.getLogger(__name__)


def create_respond_node() -> Callable[[RouterState], Awaitable[dict[str, Any]]]:
    async def respond(state: RouterState) -> dict:
        text = state.get("result_text") or "Builder produced no response."
        route = state.get("route", "?")
        logger.info("Router respond: route=%s len=%d", route, len(text))
        return {"result_text": text}

    return respond
