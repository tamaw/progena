"""
Telemetry Context - Async-safe context propagation.

Manages trace context across async boundaries using contextvars.
"""

import contextvars
import uuid
from dataclasses import dataclass, field
from typing import Optional, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .tracer import Span

# Context variables for trace propagation
_current_span: contextvars.ContextVar[Optional["Span"]] = contextvars.ContextVar(
    "current_span", default=None
)
_trace_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "trace_id", default=None
)


def get_current_span() -> Optional["Span"]:
    """Get the current active span."""
    return _current_span.get()


def set_current_span(span: Optional["Span"]) -> contextvars.Token:
    """Set the current active span. Returns token for restoration."""
    return _current_span.set(span)


def get_trace_id() -> Optional[str]:
    """Get the current trace ID."""
    return _trace_id.get()


def set_trace_id(trace_id: str) -> contextvars.Token:
    """Set the trace ID for the current context."""
    return _trace_id.set(trace_id)


def generate_trace_id() -> str:
    """Generate a new trace ID (32-character hex string, OTLP style)."""
    return uuid.uuid4().hex


def generate_span_id() -> str:
    """Generate a new span ID (16-character hex string, OTLP style)."""
    return uuid.uuid4().hex[:16]


@dataclass
class TelemetryContext:
    """
    Telemetry context container for passing context across boundaries.

    This is useful for:
    - Passing trace context to sub-agents
    - Serializing context for A2A calls
    - Restoring context after async operations
    """
    trace_id: str
    span_id: str
    parent_span_id: Optional[str] = None
    baggage: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for transport."""
        return {
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "baggage": self.baggage,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TelemetryContext":
        """Deserialize from dict."""
        return cls(
            trace_id=data.get("trace_id", generate_trace_id()),
            span_id=data.get("span_id", generate_span_id()),
            parent_span_id=data.get("parent_span_id"),
            baggage=data.get("baggage", {}),
        )

    @classmethod
    def current(cls) -> "TelemetryContext":
        """Create context from current span."""
        span = get_current_span()
        if span:
            return cls(
                trace_id=span.trace_id,
                span_id=span.span_id,
                parent_span_id=span.parent_span_id,
                baggage=dict(span.attributes) if span.attributes else {},
            )
        return cls(
            trace_id=get_trace_id() or generate_trace_id(),
            span_id=generate_span_id(),
        )

    def inject_headers(self) -> dict[str, str]:
        """Create W3C Trace Context headers."""
        # traceparent format: 00-{trace_id}-{span_id}-{flags}
        traceparent = f"00-{self.trace_id}-{self.span_id}-01"
        headers = {"traceparent": traceparent}

        # Add baggage if present
        if self.baggage:
            baggage_items = [f"{k}={v}" for k, v in self.baggage.items()]
            headers["baggage"] = ",".join(baggage_items)

        return headers

    @classmethod
    def extract_headers(cls, headers: dict[str, str]) -> "TelemetryContext":
        """Extract context from W3C Trace Context headers."""
        traceparent = headers.get("traceparent", "")
        baggage_str = headers.get("baggage", "")

        # Parse traceparent: 00-{trace_id}-{span_id}-{flags}
        trace_id = generate_trace_id()
        parent_span_id = None

        if traceparent:
            parts = traceparent.split("-")
            if len(parts) >= 3:
                trace_id = parts[1]
                parent_span_id = parts[2]

        # Parse baggage
        baggage = {}
        if baggage_str:
            for item in baggage_str.split(","):
                if "=" in item:
                    k, v = item.split("=", 1)
                    baggage[k.strip()] = v.strip()

        return cls(
            trace_id=trace_id,
            span_id=generate_span_id(),
            parent_span_id=parent_span_id,
            baggage=baggage,
        )
