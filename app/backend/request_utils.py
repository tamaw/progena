"""Shared backend route plumbing for UI event broadcasts and request IDs."""

from __future__ import annotations

from typing import Any, Literal, Protocol

from fastapi import Request

from .chat_sessions import UiEventEnvelope


class BroadcastUiEvent(Protocol):
    """Callable contract implemented by the canonical UI event broadcaster."""

    def __call__(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        scope: Literal["global", "session", "chat"] = "global",
        session_id: str | None = None,
        request_id: str | None = None,
    ) -> UiEventEnvelope: ...


def request_correlation_id(request: Request, header_request_id: str | None) -> str:
    """Resolve the middleware-correlated request ID for a finite request.

    Finite UI routes require the request-ID middleware. Callers that intentionally
    support operation-local fallback IDs (chat and response routes) should catch
    ``RuntimeError`` and generate their UUID explicitly.
    """
    request_id = header_request_id or getattr(request.state, "request_id", None)
    if not isinstance(request_id, str) or not request_id:
        raise RuntimeError("Request correlation ID middleware is not configured")
    return request_id
