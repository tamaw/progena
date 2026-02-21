"""
State Logger - JSONL logging with redaction and correlation.

ARCH-006: Split StateManager into focused responsibilities.

This module handles:
- Structured JSONL logging
- Secret redaction
- Request ID correlation
- Debug state snapshots (OBS-004)

For persistence, uses StatePersistence.
"""

import json
import logging
from typing import Any, cast

from ..runtime.time_utils import utcnow_isoformat
from .models import ConversationMessage, redact_secrets
from .persistence import StatePersistence

logger = logging.getLogger(__name__)


# Log categories
LOG_CATEGORIES = {
    "conversation": "conversation.jsonl",
    "decisions": "decisions.jsonl",
    "copilot_calls": "copilot_calls.jsonl",
    "validation": "validation.jsonl",
    "schedule": "schedule.jsonl",
    "debug_snapshots": "debug_snapshots.jsonl",  # OBS-004
    "errors": "errors.jsonl",
}


class StateLogger:
    """JSONL logging with redaction and request correlation.

    Provides:
    - Category-based logging
    - Automatic secret redaction
    - Timestamp addition
    - Request ID correlation
    - Debug state snapshots (OBS-004)
    """

    def __init__(
        self,
        persistence: StatePersistence,
        default_request_id: str | None = None,
    ):
        """Initialize state logger.

        Args:
            persistence: StatePersistence instance for file operations.
            default_request_id: Default request ID if not provided per-call.
        """
        self._persistence = persistence
        self._default_request_id = default_request_id

    async def log(
        self,
        category: str,
        entry: dict[str, Any],
        request_id: str | None = None,
        redact: bool = True,
    ) -> None:
        """Log an entry to a category.

        Args:
            category: Log category (e.g., 'decisions', 'conversation').
            entry: Entry data.
            request_id: Optional request ID for correlation.
            redact: Whether to redact secrets.
        """
        if category not in LOG_CATEGORIES:
            raise ValueError(f"Unknown log category: {category}")

        filename = LOG_CATEGORIES[category]

        # Add timestamp if not present
        if "timestamp" not in entry:
            entry["timestamp"] = utcnow_isoformat()

        # Add request ID for correlation
        if request_id or self._default_request_id:
            entry["request_id"] = request_id or self._default_request_id

        # Redact secrets
        if redact:
            # Convert to JSON, redact, then back to dict
            json_str = json.dumps(entry, default=str)
            json_str = redact_secrets(json_str)
            entry = json.loads(json_str)

        await self._persistence.append_jsonl(filename, entry)

    async def log_conversation(
        self,
        message: ConversationMessage,
        request_id: str | None = None,
    ) -> None:
        """Log a conversation message.

        Args:
            message: The conversation message.
            request_id: Optional request ID.
        """
        await self.log(
            "conversation",
            message.model_dump(),
            request_id=request_id,
        )

    async def log_decision(
        self,
        decision: dict[str, Any],
        request_id: str | None = None,
    ) -> None:
        """Log a decision.

        Args:
            decision: Decision data.
            request_id: Optional request ID.
        """
        await self.log(
            "decisions",
            decision,
            request_id=request_id,
        )

    async def log_copilot_call(
        self,
        call_data: dict[str, Any],
        request_id: str | None = None,
    ) -> None:
        """Log a Copilot CLI call.

        Args:
            call_data: Call data.
            request_id: Optional request ID.
        """
        await self.log(
            "copilot_calls",
            call_data,
            request_id=request_id,
        )

    async def log_validation(
        self,
        result: dict[str, Any],
        request_id: str | None = None,
    ) -> None:
        """Log a validation result.

        Args:
            result: Validation result.
            request_id: Optional request ID.
        """
        await self.log(
            "validation",
            result,
            request_id=request_id,
        )

    async def log_schedule_event(
        self,
        event: dict[str, Any],
        request_id: str | None = None,
    ) -> None:
        """Log a schedule event.

        Args:
            event: Schedule event data.
            request_id: Optional request ID.
        """
        await self.log(
            "schedule",
            event,
            request_id=request_id,
        )

    async def log_error(
        self,
        error: Exception,
        context: dict[str, Any] | None = None,
        request_id: str | None = None,
    ) -> None:
        """Log an error with context.

        Args:
            error: The exception.
            context: Additional context.
            request_id: Optional request ID.
        """
        entry = {
            "error_type": type(error).__name__,
            "error_message": str(error),
            "context": context or {},
        }
        await self.log(
            "errors",
            entry,
            request_id=request_id,
        )

    async def dump_state_for_debug(
        self,
        state: dict[str, Any],
        event: str,
        request_id: str | None = None,
    ) -> None:
        """Save state snapshot for debugging (OBS-004).

        Args:
            state: The state to snapshot.
            event: Event name/description.
            request_id: Optional request ID.
        """
        # Sanitize state for debug (truncate large fields)
        sanitized = self._sanitize_for_debug(state)

        entry = {
            "event": event,
            "state": sanitized,
        }

        await self.log(
            "debug_snapshots",
            entry,
            request_id=request_id,
            redact=True,
        )

        logger.debug("Saved debug snapshot for event: %s", event)

    def _sanitize_for_debug(
        self,
        state: dict[str, Any],
        max_string_length: int = 500,
        max_list_items: int = 10,
    ) -> dict[str, Any]:
        """Sanitize state for debug logging.

        - Truncates long strings
        - Limits list sizes
        - Removes binary data

        Args:
            state: State to sanitize.
            max_string_length: Max string length before truncation.
            max_list_items: Max list items to keep.

        Returns:
            Sanitized state copy.
        """
        def sanitize_value(value: Any, depth: int = 0) -> Any:
            if depth > 5:
                return "<max depth>"

            if isinstance(value, str):
                if len(value) > max_string_length:
                    return value[:max_string_length] + f"... (truncated {len(value) - max_string_length} chars)"
                return value

            if isinstance(value, bytes):
                return f"<bytes: {len(value)} bytes>"

            if isinstance(value, list):
                truncated = value[:max_list_items]
                result = [sanitize_value(v, depth + 1) for v in truncated]
                if len(value) > max_list_items:
                    result.append(f"... ({len(value) - max_list_items} more items)")
                return result

            if isinstance(value, dict):
                return {
                    k: sanitize_value(v, depth + 1)
                    for k, v in value.items()
                }

            # Handle Pydantic models
            if hasattr(value, "model_dump"):
                return sanitize_value(value.model_dump(), depth + 1)

            return value

        return cast(dict[str, Any], sanitize_value(state))

    async def read_recent(
        self,
        category: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Read recent log entries.

        Args:
            category: Log category.
            limit: Maximum entries to return.

        Returns:
            List of recent entries.
        """
        if category not in LOG_CATEGORIES:
            raise ValueError(f"Unknown log category: {category}")

        filename = LOG_CATEGORIES[category]
        return await self._persistence.read_jsonl(filename, limit)

    async def read_conversation(
        self,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Read recent conversation messages.

        Args:
            limit: Maximum messages to return.

        Returns:
            List of conversation messages.
        """
        return await self.read_recent("conversation", limit)

    async def read_decisions(
        self,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Read recent decisions.

        Args:
            limit: Maximum entries to return.

        Returns:
            List of decision entries.
        """
        return await self.read_recent("decisions", limit)
