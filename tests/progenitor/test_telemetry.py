"""
Tests for the Progenitor Telemetry module.

Tests cover:
- Metrics collection (Counter, Gauge, Histogram)
- Tracer and Span management
- JSONL export
- Instrumentation decorators
- Context propagation
"""

import asyncio
import json
import tempfile
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


class TestMetrics:
    """Tests for metrics collection."""

    def test_counter_increment(self):
        """Counter should increment correctly."""
        from app.progenitor.telemetry.metrics import Counter

        counter = Counter("test_counter", "Test counter")
        assert counter.get_value() == 0.0

        counter.inc()
        assert counter.get_value() == 1.0

        counter.inc(5)
        assert counter.get_value() == 6.0

    def test_gauge_set_and_inc_dec(self):
        """Gauge should support set, inc, and dec operations."""
        from app.progenitor.telemetry.metrics import Gauge

        gauge = Gauge("test_gauge", "Test gauge")

        gauge.set(10.0)
        assert gauge.get_value() == 10.0

        gauge.inc(5)
        assert gauge.get_value() == 15.0

        gauge.dec(3)
        assert gauge.get_value() == 12.0

    def test_histogram_observe(self):
        """Histogram should record observations correctly."""
        from app.progenitor.telemetry.metrics import Histogram

        histogram = Histogram("test_histogram", "Test histogram")

        histogram.observe(0.1)
        histogram.observe(0.5)
        histogram.observe(1.0)

        stats = histogram.get_stats()
        assert stats["count"] == 3
        assert stats["sum"] == pytest.approx(1.6, rel=0.01)
        assert stats["min"] == pytest.approx(0.1, rel=0.01)
        assert stats["max"] == pytest.approx(1.0, rel=0.01)

    def test_metrics_collector(self):
        """MetricsCollector should manage multiple metrics."""
        from app.progenitor.telemetry.metrics import MetricsCollector

        collector = MetricsCollector(prefix="test")

        counter = collector.counter("requests", "Total requests")
        gauge = collector.gauge("active", "Active connections")
        histogram = collector.histogram("latency", "Request latency")

        counter.inc()
        gauge.set(5)
        histogram.observe(0.1)

        points = collector.collect()
        assert len(points) >= 3

        names = [p.name for p in points]
        assert "test_requests" in names
        assert "test_active" in names
        assert "test_latency" in names

    def test_prometheus_export(self):
        """MetricsCollector should export to Prometheus format."""
        from app.progenitor.telemetry.metrics import MetricsCollector

        collector = MetricsCollector(prefix="prom_test")
        counter = collector.counter("total", "Total count")
        counter.inc(42)

        prometheus_text = collector.to_prometheus()

        assert "# HELP prom_test_total Total count" in prometheus_text
        assert "# TYPE prom_test_total counter" in prometheus_text
        assert "prom_test_total 42" in prometheus_text

    def test_convenience_increment(self):
        """MetricsCollector.increment() should create and increment counters."""
        from app.progenitor.telemetry.metrics import MetricsCollector

        collector = MetricsCollector(prefix="test")

        # First call creates the counter
        collector.increment("requests_total")
        collector.increment("requests_total", amount=2)
        collector.increment("requests_total", labels={"method": "GET"})

        points = collector.collect()
        names = [p.name for p in points]
        assert "test_requests_total" in names

    def test_convenience_record_histogram(self):
        """MetricsCollector.record_histogram() should record values."""
        from app.progenitor.telemetry.metrics import MetricsCollector

        collector = MetricsCollector(prefix="test")

        collector.record_histogram("latency_seconds", 0.1)
        collector.record_histogram("latency_seconds", 0.5)
        collector.record_histogram("latency_seconds", 1.0, labels={"endpoint": "/api"})

        points = collector.collect()
        names = [p.name for p in points]
        assert "test_latency_seconds" in names

    def test_convenience_set_gauge(self):
        """MetricsCollector.set_gauge() should set gauge values."""
        from app.progenitor.telemetry.metrics import MetricsCollector

        collector = MetricsCollector(prefix="test")

        collector.set_gauge("active_connections", 10)
        collector.set_gauge("active_connections", 15)

        # Get the gauge directly to check value
        gauge = collector.gauge("active_connections")
        assert gauge.get_value() == 15.0


class TestTracer:
    """Tests for tracing functionality."""

    def test_span_creation(self):
        """Tracer should create spans with proper attributes."""
        from app.progenitor.telemetry.tracer import Tracer, SpanKind

        tracer = Tracer("test")
        span = tracer.start_span("test_operation", kind=SpanKind.INTERNAL)

        assert span.name == "test_operation"
        assert span.kind == SpanKind.INTERNAL
        assert span.trace_id is not None
        assert span.span_id is not None

        tracer.end_span(span)
        assert span.end_time is not None
        assert span.duration_ms is not None

    def test_span_context_manager(self):
        """Tracer should support context manager pattern."""
        from app.progenitor.telemetry.tracer import Tracer, SpanStatus

        tracer = Tracer("test")

        with tracer.span("sync_operation") as span:
            span.set_attribute("key", "value")

        assert span.status == SpanStatus.OK
        assert span.attributes["key"] == "value"

    @pytest.mark.asyncio
    async def test_async_span_context_manager(self):
        """Tracer should support async context manager pattern."""
        from app.progenitor.telemetry.tracer import Tracer, SpanStatus

        tracer = Tracer("test")

        async with tracer.aspan("async_operation") as span:
            span.set_attribute("async", True)
            await asyncio.sleep(0.01)

        assert span.status == SpanStatus.OK
        assert span.attributes["async"] is True

    def test_span_exception_recording(self):
        """Span should record exceptions properly."""
        from app.progenitor.telemetry.tracer import Tracer, SpanStatus

        tracer = Tracer("test")
        recorded_span: Any = None

        try:
            with tracer.span("failing_operation") as span:
                recorded_span = span
                raise ValueError("Test error")
        except ValueError:
            pass

        assert recorded_span is not None
        assert recorded_span.status == SpanStatus.ERROR
        assert len(recorded_span.events) > 0
        assert recorded_span.events[0].name == "exception"

    def test_span_parent_child_relationship(self):
        """Spans should maintain parent-child relationships."""
        from app.progenitor.telemetry.tracer import Tracer

        tracer = Tracer("test")

        with tracer.span("parent") as parent_span:
            with tracer.span("child") as child_span:
                pass

        assert child_span.trace_id == parent_span.trace_id
        assert child_span.parent_span_id == parent_span.span_id


class TestContext:
    """Tests for telemetry context propagation."""

    def test_context_serialization(self):
        """TelemetryContext should serialize/deserialize correctly."""
        from app.progenitor.telemetry.context import TelemetryContext

        context = TelemetryContext(
            trace_id="abc123",
            span_id="def456",
            parent_span_id="ghi789",
            baggage={"key": "value"},
        )

        data = context.to_dict()
        restored = TelemetryContext.from_dict(data)

        assert restored.trace_id == context.trace_id
        assert restored.span_id == context.span_id
        assert restored.parent_span_id == context.parent_span_id
        assert restored.baggage == context.baggage

    def test_w3c_traceparent_injection(self):
        """TelemetryContext should inject W3C Trace Context headers."""
        from app.progenitor.telemetry.context import TelemetryContext

        context = TelemetryContext(
            trace_id="abc123",
            span_id="def456",
        )

        headers = context.inject_headers()

        assert "traceparent" in headers
        assert "abc123" in headers["traceparent"]
        assert "def456" in headers["traceparent"]

    def test_w3c_traceparent_extraction(self):
        """TelemetryContext should extract from W3C Trace Context headers."""
        from app.progenitor.telemetry.context import TelemetryContext

        headers = {
            "traceparent": "00-abc123def456789012345678901234-1234567890123456-01",
        }

        context = TelemetryContext.extract_headers(headers)

        assert context.trace_id == "abc123def456789012345678901234"
        assert context.parent_span_id == "1234567890123456"


class TestJSONLExporter:
    """Tests for JSONL export functionality."""

    def test_span_export(self):
        """JSONLExporter should write spans to JSONL file."""
        from app.progenitor.telemetry.exporter import JSONLExporter
        from app.progenitor.telemetry.tracer import Tracer

        with tempfile.TemporaryDirectory() as tmpdir:
            exporter = JSONLExporter(tmpdir, buffer_size=1)
            tracer = Tracer("test", on_span_end=exporter.export_span)

            with tracer.span("test_span") as span:
                span.set_attribute("test", True)

            exporter.flush()

            traces_file = Path(tmpdir) / "traces.jsonl"
            assert traces_file.exists()

            with open(traces_file) as f:
                line = f.readline()
                data = json.loads(line)

                assert data["type"] == "span"
                assert data["data"]["name"] == "test_span"
                assert data["data"]["attributes"]["test"] is True

            exporter.shutdown()

    def test_metric_export(self):
        """JSONLExporter should write metrics to JSONL file."""
        from app.progenitor.telemetry.exporter import JSONLExporter
        from app.progenitor.telemetry.metrics import Counter, MetricKind

        with tempfile.TemporaryDirectory() as tmpdir:
            exporter = JSONLExporter(tmpdir, buffer_size=1)

            counter = Counter("test_metric", "Test metric")
            counter.inc(42)

            exporter.export_metric(counter.to_point())
            exporter.flush()

            metrics_file = Path(tmpdir) / "metrics.jsonl"
            assert metrics_file.exists()

            with open(metrics_file) as f:
                line = f.readline()
                data = json.loads(line)

                assert data["type"] == "metric"
                assert data["data"]["name"] == "test_metric"
                assert data["data"]["value"] == 42

            exporter.shutdown()


class TestInstrumentation:
    """Tests for instrumentation decorators and helpers."""

    @pytest.mark.asyncio
    async def test_instrument_node(self):
        """instrument_node should create spans for node functions."""
        from app.progenitor.telemetry.instrument import instrument_node
        from app.progenitor.telemetry.tracer import set_tracer, Tracer
        from app.progenitor.telemetry.metrics import MetricsCollector

        # Set up fresh tracer and metrics
        spans_captured = []
        tracer = Tracer("test", on_span_end=lambda s: spans_captured.append(s))
        set_tracer(tracer)

        @instrument_node("test_node")
        async def my_node(state):
            return {"result": "done"}

        result = await my_node({"input": "test"})

        assert result == {"result": "done"}
        assert len(spans_captured) == 1
        assert "test_node" in spans_captured[0].name

    def test_iteration_telemetry(self):
        """IterationTelemetry should track iteration lifecycle."""
        from app.progenitor.telemetry.instrument import IterationTelemetry
        from app.progenitor.telemetry.tracer import set_tracer, Tracer

        spans_captured = []
        tracer = Tracer("test", on_span_end=lambda s: spans_captured.append(s))
        set_tracer(tracer)

        telem = IterationTelemetry(iteration=1, work_item_id="test-123")
        telem.start()

        telem.start_phase("observe")
        telem.end_phase("observe")

        telem.start_phase("think")
        telem.end_phase("think")

        telem.record_verification(score=0.85, passed=True, gaps=[])
        telem.complete()

        # Should have spans for iteration + 2 phases
        assert len(spans_captured) >= 3

    def test_agent_telemetry(self):
        """AgentTelemetry should track sub-agent lifecycle."""
        from app.progenitor.telemetry.instrument import AgentTelemetry
        from app.progenitor.telemetry.tracer import set_tracer, Tracer
        from app.progenitor.telemetry.metrics import MetricsCollector, get_metrics

        spans_captured = []
        tracer = Tracer("test", on_span_end=lambda s: spans_captured.append(s))
        set_tracer(tracer)

        metrics = get_metrics()
        initial_spawned = metrics.agent_spawned.get_value()

        agent_telem = AgentTelemetry("test_agent", "agent-123")
        agent_telem.start()
        agent_telem.add_event("processing")
        agent_telem.set_attribute("custom", "value")
        agent_telem.complete(success=True)

        assert len(spans_captured) == 1
        assert spans_captured[0].attributes["agent.name"] == "test_agent"
        assert metrics.agent_spawned.get_value() == initial_spawned + 1


class TestSetupAndShutdown:
    """Tests for telemetry setup and shutdown."""

    def test_setup_telemetry(self):
        """setup_telemetry should initialize tracer and metrics."""
        from app.progenitor.telemetry import setup_telemetry, shutdown_telemetry

        with tempfile.TemporaryDirectory() as tmpdir:
            tracer, metrics, exporter = setup_telemetry(tmpdir)

            assert tracer is not None
            assert metrics is not None
            assert exporter is not None

            shutdown_telemetry(exporter)

    def test_global_metrics_singleton(self):
        """get_metrics should return same instance."""
        from app.progenitor.telemetry.metrics import get_metrics

        metrics1 = get_metrics()
        metrics2 = get_metrics()

        assert metrics1 is metrics2
