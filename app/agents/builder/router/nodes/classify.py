"""Router classify node — Azure OpenAI picks the specialist route."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from pydantic import BaseModel, Field

from ...client import BuilderClient
from ...prompts import load_prompt
from ..state import RouterState

logger = logging.getLogger(__name__)


class RouteDecision(BaseModel):
    """Schema returned by the router classifier."""

    route: Literal["plan", "build", "validate"] = Field(
        description="The internal subagent the request must be dispatched to.",
    )
    reason: str = Field(
        default="",
        description="One-sentence justification for the chosen route.",
    )


def create_classify_node(
    client: BuilderClient,
) -> Callable[[RouterState], Awaitable[dict[str, Any]]]:
    """Return a router node that classifies the incoming request via Azure OpenAI."""

    system_prompt = load_prompt("router_system.md").strip()

    async def classify(state: RouterState) -> dict:
        route = state.get("route")
        if route:
            logger.info("Router: explicit route=%s, skipping classification", route)
            return {}

        request_text = (state.get("request_text") or "").strip()
        if not request_text:
            return {"route": "build", "route_reason": "empty request defaults to build"}

        decision, _reasoning = await client.chat_structured_with_reasoning(
            messages=[{"role": "user", "content": request_text}],
            response_model=RouteDecision,
            schema_name="builder_route_decision",
            system_prompt=system_prompt,
            max_tokens=512,
        )
        logger.info(
            "Router classified request: route=%s reason=%s",
            decision.route,
            decision.reason[:160],
        )
        return {"route": decision.route, "route_reason": decision.reason}

    return classify
