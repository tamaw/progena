"""
Telemetry Exporters - Export metrics and traces to various formats.

Supports:
- JSONL (primary for this implementation)
- Prometheus text format (via MetricsCollector)
- Console (for debugging)
"""

import json
import asyncio
import logging
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from queue import Queue, Empty
from typing import Any, Optional, Union

from .metrics import MetricPoint, MetricsCollector, get_metrics
from .tracer import Span, get_tracer, set_tracer, Tracer
from ..runtime.time_utils import utcnow_isoformat

logger = logging.getLogger(__name__)


class Exporter(ABC):
    """Base exporter interface."""

    @abstractmethod
    def export_span(self, span: Span) -> None:
        """Export a single span."""
        pass

    @abstractmethod
    def export_metric(self, metric: MetricPoint) -> None:
        """Export a single metric point."""
        pass

    @abstractmethod
    def flush(self) -> None:
        """Flush any buffered data."""
        pass

    @abstractmethod
    def shutdown(self) -> None:
        """Clean shutdown of exporter."""
        pass


class JSONLExporter(Exporter):
    """
    Exports telemetry data to JSONL files.

    Creates separate files for:
    - traces.jsonl: Span data
    - metrics.jsonl: Metric data

    Each line is a self-contained JSON object with type discriminator.
    """

    def __init__(
        self,
        output_dir: Union[str, Path],
        traces_file: str = "traces.jsonl",
        metrics_file: str = "metrics.jsonl",
        buffer_size: int = 100,
        flush_interval: float = 5.0,
    ):
        """
        Initialize JSONL exporter.

        Args:
            output_dir: Directory for output files
            traces_file: Name of traces file
            metrics_file: Name of metrics file
            buffer_size: Number of items to buffer before auto-flush
            flush_interval: Seconds between automatic flushes
        """
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.traces_path = self.output_dir / traces_file
        self.metrics_path = self.output_dir / metrics_file

        self.buffer_size = buffer_size
        self.flush_interval = flush_interval

        self._span_buffer: list[dict] = []
        self._metric_buffer: list[dict] = []
        self._lock = threading.Lock()

        self._shutdown = False
        self._flush_thread: Optional[threading.Thread] = None

        # Start background flush thread
        self._start_flush_thread()

    def _start_flush_thread(self) -> None:
        """Start background thread for periodic flushing."""
        def flush_loop():
            while not self._shutdown:
                try:
                    asyncio.run(asyncio.sleep(self.flush_interval))
                except RuntimeError:
                    # No event loop available, fall back to sync sleep
                    import time
                    time.sleep(self.flush_interval)
                if not self._shutdown:
                    self.flush()

        self._flush_thread = threading.Thread(target=flush_loop, daemon=True)
        self._flush_thread.start()

    def export_span(self, span: Span) -> None:
        """Export a span to the buffer."""
        record = {
            "type": "span",
            "timestamp": utcnow_isoformat(),
            "data": span.to_dict(),
        }

        with self._lock:
            self._span_buffer.append(record)
            if len(self._span_buffer) >= self.buffer_size:
                self._flush_spans()

    def export_metric(self, metric: MetricPoint) -> None:
        """Export a metric point to the buffer."""
        record = {
            "type": "metric",
            "timestamp": utcnow_isoformat(),
            "data": metric.to_dict(),
        }

        with self._lock:
            self._metric_buffer.append(record)
            if len(self._metric_buffer) >= self.buffer_size:
                self._flush_metrics()

    def _flush_spans(self) -> None:
        """Write buffered spans to file (caller must hold lock)."""
        if not self._span_buffer:
            return

        try:
            with open(self.traces_path, "a", encoding="utf-8") as f:
                for record in self._span_buffer:
                    f.write(json.dumps(record) + "\n")
            self._span_buffer.clear()
        except Exception as e:
            logger.error("Failed to flush spans: %s", e)

    def _flush_metrics(self) -> None:
        """Write buffered metrics to file (caller must hold lock)."""
        if not self._metric_buffer:
            return

        try:
            with open(self.metrics_path, "a", encoding="utf-8") as f:
                for record in self._metric_buffer:
                    f.write(json.dumps(record) + "\n")
            self._metric_buffer.clear()
        except Exception as e:
            logger.error("Failed to flush metrics: %s", e)

    def flush(self) -> None:
        """Flush all buffered data."""
        with self._lock:
            self._flush_spans()
            self._flush_metrics()

    def shutdown(self) -> None:
        """Shutdown the exporter."""
        self._shutdown = True
        self.flush()
        if self._flush_thread:
            self._flush_thread.join(timeout=2.0)


class MetricsExporter:
    """
    Periodic metrics exporter.

    Collects metrics at intervals and exports via configured exporter.
    """

    def __init__(
        self,
        exporter: Exporter,
        collector: Optional[MetricsCollector] = None,
        interval: float = 15.0,
    ):
        """
        Initialize metrics exporter.

        Args:
            exporter: Exporter to use for output
            collector: Metrics collector (uses global if not provided)
            interval: Seconds between metric collections
        """
        self.exporter = exporter
        self.collector = collector or get_metrics()
        self.interval = interval

        self._shutdown = False
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Start periodic metric collection."""
        def collect_loop():
            import time
            while not self._shutdown:
                time.sleep(self.interval)
                if not self._shutdown:
                    self._collect()

        self._thread = threading.Thread(target=collect_loop, daemon=True)
        self._thread.start()

    def _collect(self) -> None:
        """Collect and export all metrics."""
        try:
            points = self.collector.collect()
            for point in points:
                self.exporter.export_metric(point)
        except Exception as e:
            logger.error("Failed to collect metrics: %s", e)

    def stop(self) -> None:
        """Stop periodic collection."""
        self._shutdown = True
        if self._thread:
            self._thread.join(timeout=2.0)
        # Final collection
        self._collect()


class ConsoleExporter(Exporter):
    """Debug exporter that prints to console."""

    def __init__(self, pretty: bool = True):
        self.pretty = pretty

    def export_span(self, span: Span) -> None:
        data = span.to_dict()
        if self.pretty:
            print(f"[SPAN] {span.name} ({span.duration_ms:.2f}ms)")
            print(f"       trace_id={span.trace_id[:8]}... span_id={span.span_id}")
            if span.attributes:
                print(f"       attrs={span.attributes}")
        else:
            print(json.dumps({"type": "span", "data": data}))

    def export_metric(self, metric: MetricPoint) -> None:
        if self.pretty:
            print(f"[METRIC] {metric.name}={metric.value} ({metric.kind.value})")
        else:
            print(json.dumps({"type": "metric", "data": metric.to_dict()}))

    def flush(self) -> None:
        pass

    def shutdown(self) -> None:
        pass


class CompositeExporter(Exporter):
    """Exports to multiple exporters."""

    def __init__(self, exporters: list[Exporter]):
        self.exporters = exporters

    def export_span(self, span: Span) -> None:
        for exporter in self.exporters:
            try:
                exporter.export_span(span)
            except Exception as e:
                logger.error("Exporter %s failed: %s", type(exporter).__name__, e)

    def export_metric(self, metric: MetricPoint) -> None:
        for exporter in self.exporters:
            try:
                exporter.export_metric(metric)
            except Exception as e:
                logger.error("Exporter %s failed: %s", type(exporter).__name__, e)

    def flush(self) -> None:
        for exporter in self.exporters:
            exporter.flush()

    def shutdown(self) -> None:
        for exporter in self.exporters:
            exporter.shutdown()


def setup_telemetry(
    output_dir: Union[str, Path],
    enable_console: bool = False,
    metrics_interval: float = 15.0,
) -> tuple[Tracer, MetricsCollector, Exporter]:
    """
    Set up telemetry with JSONL export.

    Args:
        output_dir: Directory for telemetry files
        enable_console: Also print to console
        metrics_interval: Seconds between metric snapshots

    Returns:
        Tuple of (tracer, metrics_collector, exporter)
    """
    # Create exporter(s)
    jsonl_exporter = JSONLExporter(output_dir)

    if enable_console:
        exporter = CompositeExporter([jsonl_exporter, ConsoleExporter()])
    else:
        exporter = jsonl_exporter

    # Create tracer with export callback
    tracer = Tracer(on_span_end=exporter.export_span)
    set_tracer(tracer)

    # Get/create metrics collector
    metrics = get_metrics()

    # Start metrics exporter
    metrics_exporter = MetricsExporter(exporter, metrics, metrics_interval)
    metrics_exporter.start()

    # Store reference for shutdown
    exporter._metrics_exporter = metrics_exporter  # type: ignore

    return tracer, metrics, exporter


def shutdown_telemetry(exporter: Exporter) -> None:
    """Shutdown telemetry cleanly."""
    # Stop metrics collection
    metrics_exporter = getattr(exporter, "_metrics_exporter", None)
    if metrics_exporter is not None:
        metrics_exporter.stop()

    # Shutdown exporter
    exporter.shutdown()
