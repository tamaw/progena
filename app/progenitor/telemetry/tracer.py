"""
Tracer - OpenTelemetry-style distributed tracing.

Provides:
- Span creation with parent/child relationships
- Automatic context propagation
- Event and attribute recording
- Integration with metrics
"""

import time
import logging
import asyncio
from contextlib import contextmanager, asynccontextmanager
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional, Generator, AsyncGenerator, Callable

from .context import (
    get_current_span,
    set_current_span,
    get_trace_id,
    set_trace_id,
    generate_trace_id,
    generate_span_id,
    TelemetryContext,
)
from ..runtime.time_utils import utcnow_isoformat

logger = logging.getLogger(__name__)


class SpanKind(Enum):
    """Type of span, following OTLP conventions."""
    INTERNAL = "internal"      # Internal operation
    SERVER = "server"          # Handling incoming request
    CLIENT = "client"          # Making outgoing request
    PRODUCER = "producer"      # Producing message
    CONSUMER = "consumer"      # Consuming message


class SpanStatus(Enum):
    """Span completion status."""
    UNSET = "unset"
    OK = "ok"
    ERROR = "error"


@dataclass
class SpanEvent:
    """An event within a span."""
    name: str
    timestamp: str
    attributes: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "timestamp": self.timestamp,
            "attributes": self.attributes,
        }


@dataclass
class SpanLink:
    """Link to another span (for multi-parent scenarios)."""
    trace_id: str
    span_id: str
    attributes: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "attributes": self.attributes,
        }


@dataclass
class Span:
    """
    A span represents a single operation within a trace.

    Follows OTLP span model for compatibility with observability backends.
    """
    name: str
    trace_id: str
    span_id: str
    parent_span_id: Optional[str] = None
    kind: SpanKind = SpanKind.INTERNAL

    # Timing
    start_time: str = field(default_factory=utcnow_isoformat)
    end_time: Optional[str] = None
    duration_ms: Optional[float] = None

    # Status
    status: SpanStatus = SpanStatus.UNSET
    status_message: Optional[str] = None

    # Data
    attributes: dict[str, Any] = field(default_factory=dict)
    events: list[SpanEvent] = field(default_factory=list)
    links: list[SpanLink] = field(default_factory=list)

    # Internal tracking
    _start_perf: float = field(default_factory=time.perf_counter, repr=False)
    _ended: bool = field(default=False, repr=False)
    _token: Optional[Any] = field(default=None, repr=False)

    def set_attribute(self, key: str, value: Any) -> "Span":
        """Set a span attribute."""
        self.attributes[key] = value
        return self

    def set_attributes(self, attributes: dict[str, Any]) -> "Span":
        """Set multiple attributes."""
        self.attributes.update(attributes)
        return self

    def add_event(
        self,
        name: str,
        attributes: Optional[dict[str, Any]] = None,
    ) -> "Span":
        """Add an event to the span."""
        self.events.append(SpanEvent(
            name=name,
            timestamp=utcnow_isoformat(),
            attributes=attributes or {},
        ))
        return self

    def add_link(
        self,
        trace_id: str,
        span_id: str,
        attributes: Optional[dict[str, Any]] = None,
    ) -> "Span":
        """Add a link to another span."""
        self.links.append(SpanLink(
            trace_id=trace_id,
            span_id=span_id,
            attributes=attributes or {},
        ))
        return self

    def set_status(self, status: SpanStatus, message: Optional[str] = None) -> "Span":
        """Set the span status."""
        self.status = status
        self.status_message = message
        return self

    def record_exception(self, exception: Exception) -> "Span":
        """Record an exception in the span."""
        self.add_event(
            "exception",
            {
                "exception.type": type(exception).__name__,
                "exception.message": str(exception),
            }
        )
        self.set_status(SpanStatus.ERROR, str(exception))
        return self

    def end(self) -> None:
        """End the span."""
        if self._ended:
            return

        self._ended = True
        self.end_time = utcnow_isoformat()
        self.duration_ms = (time.perf_counter() - self._start_perf) * 1000

        # Restore previous span context
        if self._token is not None:
            set_current_span(None)  # Reset, token restoration is manual

    def is_recording(self) -> bool:
        """Check if the span is still recording."""
        return not self._ended

    def to_dict(self) -> dict[str, Any]:
        """Serialize to OTLP-style dict."""
        return {
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "name": self.name,
            "kind": self.kind.value,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_ms": self.duration_ms,
            "status": self.status.value,
            "status_message": self.status_message,
            "attributes": self.attributes,
            "events": [e.to_dict() for e in self.events],
            "links": [l.to_dict() for l in self.links],
        }

    def get_context(self) -> TelemetryContext:
        """Get telemetry context for propagation."""
        return TelemetryContext(
            trace_id=self.trace_id,
            span_id=self.span_id,
            parent_span_id=self.parent_span_id,
            baggage=dict(self.attributes),
        )


class Tracer:
    """
    Creates and manages spans.

    Provides context managers for easy span creation and automatic cleanup.
    """

    def __init__(
        self,
        name: str = "progenitor",
        on_span_end: Optional[Callable[[Span], None]] = None,
    ):
        """
        Initialize tracer.

        Args:
            name: Tracer name (usually service/component name)
            on_span_end: Callback when span ends (for export)
        """
        self.name = name
        self.on_span_end = on_span_end

    def start_span(
        self,
        name: str,
        kind: SpanKind = SpanKind.INTERNAL,
        parent: Optional[Span] = None,
        attributes: Optional[dict[str, Any]] = None,
        links: Optional[list[SpanLink]] = None,
        context: Optional[TelemetryContext] = None,
    ) -> Span:
        """
        Start a new span.

        Args:
            name: Span name (operation being performed)
            kind: Type of span
            parent: Explicit parent span (uses current if not provided)
            attributes: Initial attributes
            links: Links to other spans
            context: Context from external source (e.g., A2A call)
        """
        # Determine parent
        if context:
            trace_id = context.trace_id
            parent_span_id = context.span_id
        elif parent:
            trace_id = parent.trace_id
            parent_span_id = parent.span_id
        else:
            current = get_current_span()
            if current:
                trace_id = current.trace_id
                parent_span_id = current.span_id
            else:
                trace_id = get_trace_id() or generate_trace_id()
                parent_span_id = None

        span = Span(
            name=name,
            trace_id=trace_id,
            span_id=generate_span_id(),
            parent_span_id=parent_span_id,
            kind=kind,
            attributes=attributes or {},
            links=links or [],
        )

        # Set as current span
        span._token = set_current_span(span)
        if not get_trace_id():
            set_trace_id(trace_id)

        return span

    def end_span(self, span: Span) -> None:
        """End a span and trigger export callback."""
        span.end()
        if self.on_span_end:
            self.on_span_end(span)

    @contextmanager
    def span(
        self,
        name: str,
        kind: SpanKind = SpanKind.INTERNAL,
        attributes: Optional[dict[str, Any]] = None,
    ) -> Generator[Span, None, None]:
        """Context manager for span lifecycle."""
        span = self.start_span(name, kind, attributes=attributes)
        try:
            yield span
            if span.status == SpanStatus.UNSET:
                span.set_status(SpanStatus.OK)
        except Exception as e:
            span.record_exception(e)
            raise
        finally:
            self.end_span(span)

    @asynccontextmanager
    async def aspan(
        self,
        name: str,
        kind: SpanKind = SpanKind.INTERNAL,
        attributes: Optional[dict[str, Any]] = None,
    ) -> AsyncGenerator[Span, None]:
        """Async context manager for span lifecycle."""
        span = self.start_span(name, kind, attributes=attributes)
        try:
            yield span
            if span.status == SpanStatus.UNSET:
                span.set_status(SpanStatus.OK)
        except Exception as e:
            span.record_exception(e)
            raise
        finally:
            self.end_span(span)

    def start_as_current_span(
        self,
        name: str,
        kind: SpanKind = SpanKind.INTERNAL,
    ) -> Callable:
        """Decorator to wrap function in span."""
        def decorator(func: Callable) -> Callable:
            if asyncio.iscoroutinefunction(func):
                async def async_wrapper(*args, **kwargs):
                    async with self.aspan(name, kind) as span:
                        span.set_attribute("function", func.__name__)
                        return await func(*args, **kwargs)
                return async_wrapper
            else:
                def sync_wrapper(*args, **kwargs):
                    with self.span(name, kind) as span:
                        span.set_attribute("function", func.__name__)
                        return func(*args, **kwargs)
                return sync_wrapper
        return decorator


# Pre-defined span names for consistency
class SpanNames:
    """Standard span names for Progenitor operations."""

    # Loop operations
    LOOP_CYCLE = "loop.cycle"
    LOOP_INPUT = "loop.input"
    LOOP_CHECKPOINT = "loop.checkpoint"

    # Graph operations
    GRAPH_EXECUTE = "graph.execute"
    NODE_EXECUTE = "node.{name}"

    # Inner loop phases
    OBSERVE = "phase.observe"
    THINK = "phase.think"
    PLAN = "phase.plan"
    BUILD = "phase.build"
    EXECUTE = "phase.execute"
    VERIFY = "phase.verify"
    LEARN = "phase.learn"

    # Tool calls
    TOOL_CALL = "tool.{name}"
    COPILOT_CALL = "tool.copilot"

    # LLM calls
    LLM_CALL = "llm.call"
    LLM_STREAM = "llm.stream"

    # Agent operations
    AGENT_SPAWN = "agent.spawn"
    AGENT_EXECUTE = "agent.execute"
    AGENT_COMMUNICATE = "agent.communicate"

    # Verification
    VERIFY_CRITERIA = "verify.criteria"
    VERIFY_SCORE = "verify.score"


# Global tracer instance
_tracer: Optional[Tracer] = None


def get_tracer() -> Tracer:
    """Get or create the global tracer."""
    global _tracer
    if _tracer is None:
        _tracer = Tracer()
    return _tracer


def set_tracer(tracer: Tracer) -> None:
    """Set the global tracer."""
    global _tracer
    _tracer = tracer
