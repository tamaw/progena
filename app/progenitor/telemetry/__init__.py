"""
Progenitor Telemetry - OpenTelemetry-style Metrics and Tracing.

Provides observability for:
- Iteration counts, verification scores, tool call latency
- Sub-agent execution tracking
- Graph node transitions
- LLM call metrics

Exports to JSONL format for log aggregation and analysis.
"""

from .metrics import MetricsCollector, Counter, Histogram, Gauge, get_metrics
from .tracer import Tracer, Span, SpanKind, SpanStatus, get_tracer, set_tracer
from .exporter import JSONLExporter, MetricsExporter, setup_telemetry, shutdown_telemetry
from .context import TelemetryContext, get_current_span, set_current_span
from .instrument import (
    instrument_node,
    instrument_tool_call,
    instrument_llm_call,
    AgentTelemetry,
    IterationTelemetry,
    CycleTelemetry,
)


def get_telemetry():
    """
    Get the telemetry tracer and metrics instances.

    This is a centralized helper for accessing telemetry components.
    Returns (None, None) if telemetry is not available, allowing callers
    to gracefully handle the case where telemetry is disabled.

    Returns:
        Tuple of (tracer, metrics) where each may be None if not configured.

    Example:
        tracer, metrics = get_telemetry()
        if tracer:
            span = tracer.start_span("my_operation")
        if metrics:
            metrics.increment("my_counter")
    """
    try:
        tracer = get_tracer()
        metrics = get_metrics()
        return tracer, metrics
    except Exception:
        return None, None


__all__ = [
    # Metrics
    "MetricsCollector",
    "Counter",
    "Histogram",
    "Gauge",
    "get_metrics",
    # Tracing
    "Tracer",
    "Span",
    "SpanKind",
    "SpanStatus",
    "get_tracer",
    "set_tracer",
    # Export
    "JSONLExporter",
    "MetricsExporter",
    "setup_telemetry",
    "shutdown_telemetry",
    # Context
    "TelemetryContext",
    "get_current_span",
    "set_current_span",
    # Instrumentation
    "instrument_node",
    "instrument_tool_call",
    "instrument_llm_call",
    "AgentTelemetry",
    "IterationTelemetry",
    "CycleTelemetry",
    # Helpers
    "get_telemetry",
]
