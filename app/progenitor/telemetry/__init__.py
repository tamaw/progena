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
]
