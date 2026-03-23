"""
Lifecycle hooks for builder_agent builds.

Provides a small async hook manager so graph nodes and the background build
runner can emit structured lifecycle events without hard-coding observers.
"""

from __future__ import annotations

import inspect
import json
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

HookHandler = Any


@dataclass(slots=True)
class HookContext:
    """Structured context passed to lifecycle hooks."""

    event: str
    build_id: str | None = None
    node_name: str | None = None
    state: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


class HookManager:
    """Register and emit async lifecycle hook callbacks."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[HookHandler]] = {}

    def register(self, event: str, handler: HookHandler) -> None:
        """Register a handler for an event or '*' wildcard."""
        self._handlers.setdefault(event, []).append(handler)

    async def emit(self, event: str, context: HookContext) -> None:
        """Emit an event to matching handlers.

        Wildcard handlers registered under '*' receive all events.
        Handler failures are logged and suppressed so hooks never block builds.
        """
        handlers = [*self._handlers.get("*", []), *self._handlers.get(event, [])]
        if not handlers:
            return

        for handler in handlers:
            try:
                result = handler(context)
                if inspect.isawaitable(result):
                    await result
            except Exception:
                logger.exception("Hook handler failed for event=%s", event)


def _extract_request_id(state: dict[str, Any] | None, build_id: str | None) -> str | None:
    if not isinstance(state, dict):
        return build_id
    build_settings = state.get("build_settings")
    if isinstance(build_settings, dict):
        for key in ("build_id", "a2a_task_id", "task_id"):
            value = build_settings.get(key)
            if isinstance(value, str) and value.strip():
                return value
    task_id = state.get("task_id")
    if isinstance(task_id, str) and task_id.strip():
        return task_id
    return build_id


def _normalize_registration(spec: str) -> tuple[str, str]:
    raw = (spec or "").strip()
    if not raw:
        raise ValueError("Hook registration cannot be empty")
    if "=" in raw:
        event, handler = raw.split("=", 1)
    elif ":" in raw:
        event, handler = raw.split(":", 1)
    else:
        event, handler = "*", raw
    event = event.strip() or "*"
    handler = handler.strip()
    if not handler:
        raise ValueError(f"Hook registration is missing a handler: {spec!r}")
    return event, handler


def _build_decision_log_handler(state_manager: Any | None) -> HookHandler:
    if state_manager is None:
        async def _noop(_context: HookContext) -> None:
            return None
        return _noop

    async def _log_hook_event(context: HookContext) -> None:
        await state_manager.log_decision(
            {
                "event": "hook",
                "hook_event": context.event,
                "build_id": context.build_id,
                "node_name": context.node_name,
                "error": context.error,
                "metadata": context.metadata,
            }
        )

    return _log_hook_event


def _build_python_logger_handler(_state_manager: Any | None) -> HookHandler:
    def _log_to_python_logger(context: HookContext) -> None:
        payload = {
            "hook_event": context.event,
            "build_id": context.build_id,
            "node_name": context.node_name,
            "error": context.error,
            "metadata": context.metadata,
        }
        logger.info("builder_hook %s", json.dumps(payload, default=str))

    return _log_to_python_logger


BUILTIN_HOOK_HANDLERS: dict[str, Any] = {
    "decision_log": _build_decision_log_handler,
    "python_logger": _build_python_logger_handler,
}


def register_configured_hooks(
    manager: HookManager,
    registrations: list[str] | None,
    state_manager: Any | None = None,
) -> HookManager:
    """Register hook handlers from declarative config strings."""
    for spec in registrations or []:
        event, handler_name = _normalize_registration(spec)
        factory = BUILTIN_HOOK_HANDLERS.get(handler_name)
        if factory is None:
            raise ValueError(
                f"Unknown hook handler '{handler_name}'. "
                f"Available: {', '.join(sorted(BUILTIN_HOOK_HANDLERS))}"
            )
        manager.register(event, factory(state_manager))
    return manager


def create_default_hook_manager(
    state_manager: Any | None = None,
    registrations: list[str] | None = None,
) -> HookManager:
    """Create a hook manager from declarative registrations."""
    manager = HookManager()
    return register_configured_hooks(
        manager,
        registrations or ["*=decision_log"],
        state_manager=state_manager,
    )


def extract_build_id(state: dict[str, Any] | None) -> str | None:
    """Extract the canonical build_id from a graph/build state if present."""
    if not isinstance(state, dict):
        return None
    build_settings = state.get("build_settings")
    if isinstance(build_settings, dict):
        build_id = build_settings.get("build_id")
        if isinstance(build_id, str) and build_id.strip():
            return build_id
    return _extract_request_id(state, None)
