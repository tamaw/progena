"""
Metrics Collection - OpenTelemetry-style metrics.

Provides:
- Counter: Monotonically increasing values (e.g., total iterations)
- Gauge: Point-in-time values (e.g., current verification score)
- Histogram: Distribution of values (e.g., latency buckets)
"""

import time
import threading
import statistics
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional, Callable
from enum import Enum

from ..runtime.time_utils import utcnow_isoformat


class MetricKind(Enum):
    """Type of metric."""
    COUNTER = "counter"
    GAUGE = "gauge"
    HISTOGRAM = "histogram"


@dataclass
class MetricPoint:
    """A single metric data point."""
    name: str
    kind: MetricKind
    value: float
    timestamp: str
    labels: dict[str, str] = field(default_factory=dict)
    attributes: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to OTLP-style dict."""
        return {
            "name": self.name,
            "kind": self.kind.value,
            "value": self.value,
            "timestamp": self.timestamp,
            "labels": self.labels,
            "attributes": self.attributes,
        }


class Metric(ABC):
    """Base metric class."""

    def __init__(
        self,
        name: str,
        description: str = "",
        unit: str = "",
        labels: Optional[dict[str, str]] = None,
    ):
        self.name = name
        self.description = description
        self.unit = unit
        self.labels = labels or {}
        self._lock = threading.Lock()

    @abstractmethod
    def get_value(self) -> float:
        """Get current metric value."""
        pass

    @abstractmethod
    def get_kind(self) -> MetricKind:
        """Get metric type."""
        pass

    def to_point(self, extra_labels: Optional[dict[str, str]] = None) -> MetricPoint:
        """Create a metric point from current state."""
        combined_labels = {**self.labels, **(extra_labels or {})}
        return MetricPoint(
            name=self.name,
            kind=self.get_kind(),
            value=self.get_value(),
            timestamp=utcnow_isoformat(),
            labels=combined_labels,
            attributes={
                "description": self.description,
                "unit": self.unit,
            },
        )


class Counter(Metric):
    """Monotonically increasing counter."""

    def __init__(
        self,
        name: str,
        description: str = "",
        unit: str = "1",
        labels: Optional[dict[str, str]] = None,
    ):
        super().__init__(name, description, unit, labels)
        self._value: float = 0.0

    def inc(self, amount: float = 1.0) -> None:
        """Increment the counter."""
        with self._lock:
            self._value += amount

    def get_value(self) -> float:
        with self._lock:
            return self._value

    def get_kind(self) -> MetricKind:
        return MetricKind.COUNTER


class Gauge(Metric):
    """Point-in-time gauge value."""

    def __init__(
        self,
        name: str,
        description: str = "",
        unit: str = "1",
        labels: Optional[dict[str, str]] = None,
    ):
        super().__init__(name, description, unit, labels)
        self._value: float = 0.0

    def set(self, value: float) -> None:
        """Set the gauge value."""
        with self._lock:
            self._value = value

    def inc(self, amount: float = 1.0) -> None:
        """Increment the gauge."""
        with self._lock:
            self._value += amount

    def dec(self, amount: float = 1.0) -> None:
        """Decrement the gauge."""
        with self._lock:
            self._value -= amount

    def get_value(self) -> float:
        with self._lock:
            return self._value

    def get_kind(self) -> MetricKind:
        return MetricKind.GAUGE


class Histogram(Metric):
    """
    Histogram for measuring distributions.

    Tracks count, sum, min, max, and bucket counts.
    Default buckets match common latency distributions.
    """

    DEFAULT_BUCKETS = (
        0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0,
        2.5, 5.0, 10.0, 30.0, 60.0, float("inf"),
    )

    def __init__(
        self,
        name: str,
        description: str = "",
        unit: str = "s",
        labels: Optional[dict[str, str]] = None,
        buckets: Optional[tuple[float, ...]] = None,
    ):
        super().__init__(name, description, unit, labels)
        self._buckets = buckets or self.DEFAULT_BUCKETS
        self._bucket_counts: list[int] = [0] * len(self._buckets)
        self._count: int = 0
        self._sum: float = 0.0
        self._min: float = float("inf")
        self._max: float = float("-inf")
        self._values: list[float] = []  # Keep recent values for percentile calculation
        self._max_values = 1000  # Limit memory usage

    def observe(self, value: float) -> None:
        """Record an observation."""
        with self._lock:
            self._count += 1
            self._sum += value
            self._min = min(self._min, value)
            self._max = max(self._max, value)

            # Update bucket counts
            for i, boundary in enumerate(self._buckets):
                if value <= boundary:
                    self._bucket_counts[i] += 1
                    break

            # Track recent values for percentiles
            self._values.append(value)
            if len(self._values) > self._max_values:
                self._values = self._values[-self._max_values:]

    def get_value(self) -> float:
        """Get count (primary value for histograms)."""
        with self._lock:
            return float(self._count)

    def get_kind(self) -> MetricKind:
        return MetricKind.HISTOGRAM

    def get_stats(self) -> dict[str, Any]:
        """Get detailed histogram statistics."""
        with self._lock:
            if self._count == 0:
                return {
                    "count": 0,
                    "sum": 0.0,
                    "min": None,
                    "max": None,
                    "mean": None,
                    "p50": None,
                    "p90": None,
                    "p99": None,
                }

            sorted_values = sorted(self._values) if self._values else []

            return {
                "count": self._count,
                "sum": self._sum,
                "min": self._min if self._min != float("inf") else None,
                "max": self._max if self._max != float("-inf") else None,
                "mean": self._sum / self._count if self._count > 0 else None,
                "p50": self._percentile(sorted_values, 0.50),
                "p90": self._percentile(sorted_values, 0.90),
                "p99": self._percentile(sorted_values, 0.99),
                "buckets": dict(zip(
                    [str(b) for b in self._buckets],
                    self._bucket_counts.copy()
                )),
            }

    def _percentile(self, sorted_values: list[float], p: float) -> Optional[float]:
        """Calculate percentile from sorted values."""
        if not sorted_values:
            return None
        idx = int(len(sorted_values) * p)
        return sorted_values[min(idx, len(sorted_values) - 1)]

    def to_point(self, extra_labels: Optional[dict[str, str]] = None) -> MetricPoint:
        """Create metric point with histogram stats."""
        point = super().to_point(extra_labels)
        point.attributes.update(self.get_stats())
        return point


class Timer:
    """Context manager for timing operations."""

    def __init__(self, histogram: Histogram):
        self.histogram = histogram
        self.start_time: Optional[float] = None

    def __enter__(self) -> "Timer":
        self.start_time = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self.start_time is not None:
            elapsed = time.perf_counter() - self.start_time
            self.histogram.observe(elapsed)


class MetricsCollector:
    """
    Central metrics collector.

    Manages all metrics and provides Prometheus-compatible export.
    """

    def __init__(self, prefix: str = "progenitor"):
        self.prefix = prefix
        self._metrics: dict[str, Metric] = {}
        self._lock = threading.Lock()

        # Pre-create standard metrics
        self._init_standard_metrics()

    def _init_standard_metrics(self) -> None:
        """Initialize standard Progenitor metrics."""
        # Cycle metrics
        self.cycle_count = self.counter(
            "cycles_total",
            "Total number of loop cycles",
        )
        self.active_agents = self.gauge(
            "active_agents",
            "Number of currently running sub-agents",
        )

        # Iteration metrics
        self.iterations_total = self.counter(
            "iterations_total",
            "Total inner loop iterations",
        )
        self.iteration_duration = self.histogram(
            "iteration_duration_seconds",
            "Duration of inner loop iterations",
            unit="s",
        )

        # Verification metrics
        self.verification_score = self.gauge(
            "verification_score",
            "Current verification score",
        )
        self.verification_passed = self.counter(
            "verification_passed_total",
            "Total verifications that passed",
        )
        self.verification_failed = self.counter(
            "verification_failed_total",
            "Total verifications that failed",
        )

        # Tool call metrics
        self.tool_calls_total = self.counter(
            "tool_calls_total",
            "Total tool calls",
        )
        self.tool_call_duration = self.histogram(
            "tool_call_duration_seconds",
            "Duration of tool calls",
            unit="s",
        )
        self.tool_call_errors = self.counter(
            "tool_call_errors_total",
            "Total tool call errors",
        )

        # LLM metrics
        self.llm_calls_total = self.counter(
            "llm_calls_total",
            "Total LLM API calls",
        )
        self.llm_call_duration = self.histogram(
            "llm_call_duration_seconds",
            "Duration of LLM calls",
            unit="s",
        )
        self.llm_tokens_prompt = self.counter(
            "llm_tokens_prompt_total",
            "Total prompt tokens used",
        )
        self.llm_tokens_completion = self.counter(
            "llm_tokens_completion_total",
            "Total completion tokens used",
        )

        # Sub-agent metrics
        self.agent_spawned = self.counter(
            "agent_spawned_total",
            "Total sub-agents spawned",
        )
        self.agent_completed = self.counter(
            "agent_completed_total",
            "Total sub-agents completed",
        )
        self.agent_failed = self.counter(
            "agent_failed_total",
            "Total sub-agents failed",
        )
        self.agent_duration = self.histogram(
            "agent_duration_seconds",
            "Duration of sub-agent execution",
            unit="s",
        )

        # Graph node metrics
        self.node_executions = self.counter(
            "node_executions_total",
            "Total graph node executions",
        )
        self.node_duration = self.histogram(
            "node_duration_seconds",
            "Duration of graph node execution",
            unit="s",
        )

    def _full_name(self, name: str) -> str:
        """Get full metric name with prefix."""
        return f"{self.prefix}_{name}"

    def counter(
        self,
        name: str,
        description: str = "",
        unit: str = "1",
        labels: Optional[dict[str, str]] = None,
    ) -> Counter:
        """Create or get a counter metric."""
        full_name = self._full_name(name)
        with self._lock:
            if full_name not in self._metrics:
                self._metrics[full_name] = Counter(full_name, description, unit, labels)
            return self._metrics[full_name]  # type: ignore

    def gauge(
        self,
        name: str,
        description: str = "",
        unit: str = "1",
        labels: Optional[dict[str, str]] = None,
    ) -> Gauge:
        """Create or get a gauge metric."""
        full_name = self._full_name(name)
        with self._lock:
            if full_name not in self._metrics:
                self._metrics[full_name] = Gauge(full_name, description, unit, labels)
            return self._metrics[full_name]  # type: ignore

    def histogram(
        self,
        name: str,
        description: str = "",
        unit: str = "s",
        labels: Optional[dict[str, str]] = None,
        buckets: Optional[tuple[float, ...]] = None,
    ) -> Histogram:
        """Create or get a histogram metric."""
        full_name = self._full_name(name)
        with self._lock:
            if full_name not in self._metrics:
                self._metrics[full_name] = Histogram(
                    full_name, description, unit, labels, buckets
                )
            return self._metrics[full_name]  # type: ignore

    def timer(self, histogram: Histogram) -> Timer:
        """Create a timer context manager for the given histogram."""
        return Timer(histogram)

    def increment(self, name: str, labels: Optional[dict[str, str]] = None, amount: float = 1.0) -> None:
        """Convenience method to increment a counter by name.

        Creates the counter if it doesn't exist.

        Args:
            name: Counter name (without prefix)
            labels: Optional labels for the counter
            amount: Amount to increment (default 1.0)
        """
        counter = self.counter(name, labels=labels)
        counter.inc(amount)

    def record_histogram(self, name: str, value: float, labels: Optional[dict[str, str]] = None) -> None:
        """Convenience method to record a value in a histogram.

        Creates the histogram if it doesn't exist.

        Args:
            name: Histogram name (without prefix)
            value: Value to record
            labels: Optional labels for the histogram
        """
        histogram = self.histogram(name, labels=labels)
        histogram.observe(value)

    def set_gauge(self, name: str, value: float, labels: Optional[dict[str, str]] = None) -> None:
        """Convenience method to set a gauge value.

        Creates the gauge if it doesn't exist.

        Args:
            name: Gauge name (without prefix)
            value: Value to set
            labels: Optional labels for the gauge
        """
        gauge = self.gauge(name, labels=labels)
        gauge.set(value)

    def collect(self) -> list[MetricPoint]:
        """Collect all current metric points."""
        with self._lock:
            return [metric.to_point() for metric in self._metrics.values()]

    def to_prometheus(self) -> str:
        """Export metrics in Prometheus text format."""
        lines = []
        with self._lock:
            for metric in self._metrics.values():
                # Add HELP and TYPE
                lines.append(f"# HELP {metric.name} {metric.description}")
                lines.append(f"# TYPE {metric.name} {metric.get_kind().value}")

                # Add value
                labels_str = ""
                if metric.labels:
                    label_pairs = [f'{k}="{v}"' for k, v in metric.labels.items()]
                    labels_str = "{" + ",".join(label_pairs) + "}"

                if isinstance(metric, Histogram):
                    stats = metric.get_stats()
                    lines.append(f"{metric.name}_count{labels_str} {stats['count']}")
                    lines.append(f"{metric.name}_sum{labels_str} {stats['sum']}")
                    if stats.get('buckets'):
                        cumulative = 0
                        for bucket, count in stats['buckets'].items():
                            cumulative += count
                            bucket_labels = labels_str.replace("}", f',le="{bucket}"}}') if labels_str else f'{{le="{bucket}"}}'
                            lines.append(f"{metric.name}_bucket{bucket_labels} {cumulative}")
                else:
                    lines.append(f"{metric.name}{labels_str} {metric.get_value()}")

        return "\n".join(lines)

    def reset(self) -> None:
        """Reset all metrics (useful for testing)."""
        with self._lock:
            self._metrics.clear()
            self._init_standard_metrics()


# Global metrics collector instance
_metrics: Optional[MetricsCollector] = None


def get_metrics() -> MetricsCollector:
    """Get or create the global metrics collector."""
    global _metrics
    if _metrics is None:
        _metrics = MetricsCollector()
    return _metrics
