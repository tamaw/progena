"""Lifecycle hooks for builder_progenitor builds."""

from __future__ import annotations

import inspect
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from .client import PlannedBuild

logger = logging.getLogger(__name__)

HookHandler = Callable[["HookContext"], Awaitable[None] | None]


@dataclass(slots=True)
class HookContext:
    """Structured context passed to builder lifecycle hooks."""

    event: str
    task_id: str
    request_text: str
    request_context: dict[str, Any] = field(default_factory=dict)
    planned_build: PlannedBuild | None = None
    result_text: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


class HookManager:
    """Register and emit async lifecycle hook callbacks."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[HookHandler]] = {}

    def register(self, event: str, handler: HookHandler) -> None:
        self._handlers.setdefault(event, []).append(handler)

    async def emit(self, event: str, context: HookContext) -> None:
        handlers = [*self._handlers.get("*", []), *self._handlers.get(event, [])]
        for handler in handlers:
            try:
                result = handler(context)
                if inspect.isawaitable(result):
                    await result
            except Exception:
                logger.exception("Hook handler failed for event=%s task_id=%s", event, context.task_id)


def create_default_hook_manager(handler: HookHandler) -> HookManager:
    """Create the default lifecycle hook manager for builder_progenitor."""
    manager = HookManager()
    for event in ("build_started", "build_completed", "build_failed", "build_cancelled"):
        manager.register(event, handler)
    return manager
