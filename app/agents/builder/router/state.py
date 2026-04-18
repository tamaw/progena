"""State shapes for the Builder router graph."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Literal, TypedDict

from ..client import PlannedBuild

Route = Literal["plan", "build", "validate"]


class RouterState(TypedDict, total=False):
    """State for the top-level router graph.

    The router graph is intentionally thin: it carries the request, the
    classifier's choice, an optional pre-computed plan (set by callers that
    have already planned), the result string from whichever subagent runs,
    and a free-form metadata bag for downstream consumers.

    ``on_planned`` is an optional async callback fired by the build subagent
    immediately after it materialises a plan (so the queue worker can emit a
    ``build_started`` hook with planning metadata before execution starts).
    """

    request_text: str
    request_context: dict[str, Any]
    task_id: str
    planned_build: PlannedBuild | None
    route: Route
    route_reason: str
    result_text: str
    metadata: dict[str, Any]
    on_planned: Callable[[PlannedBuild], Awaitable[None]] | None
