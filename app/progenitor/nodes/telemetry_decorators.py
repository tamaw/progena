"""
Telemetry decorators for automatic node instrumentation.

Provides decorators that eliminate boilerplate telemetry code from nodes
by automatically adding tracing, metrics, and error recording.
"""

import functools
import logging
import time
from contextlib import contextmanager, nullcontext
from typing import Any, Callable, Coroutine, Optional, TypeVar

from ..state.models import ProgenitorState

logger = logging.getLogger(__name__)

# Type for node functions
NodeFunc = Callable[[ProgenitorState], Coroutine[Any, Any, ProgenitorState]]
T = TypeVar('T', bound=Callable)


def _get_telemetry():
    """Get telemetry components if available."""
    try:
        from ..telemetry import get_tracer, get_metrics
        return get_tracer(), get_metrics()
    except ImportError:
        return None, None


@contextmanager
def _trace_span(tracer, span_name: str, attributes: Optional[dict] = None):
    """Context manager for tracing spans with proper cleanup."""
    if tracer is None:
        yield None
        return

    span = None
    try:
        span = tracer.start_span(span_name, attributes=attributes or {})
        yield span
    except Exception:
        # Re-raise exception but ensure span is ended
        if span:
            try:
                tracer.end_span(span)
            except Exception as e:
                logger.debug("Failed to end span during exception: %s", e)
        raise
    else:
        # Normal exit - end span
        if span:
            try:
                tracer.end_span(span)
            except Exception as e:
                logger.debug("Failed to end span: %s", e)


def traced_node(node_name: str) -> Callable[[Callable[..., NodeFunc]], Callable[..., NodeFunc]]:
    """Decorator for automatic node telemetry instrumentation.

    Wraps node factory functions to automatically add:
    - Span tracing for the node execution
    - Duration histogram metrics
    - Error recording
    - Success/failure counters

    Usage:
        @traced_node("decide")
        def create_node(llm_client, tool_registry):
            async def decide_node(state: ProgenitorState) -> ProgenitorState:
                # Node implementation without telemetry boilerplate
                return {"current_decision": decision}
            return decide_node

    Args:
        node_name: Name of the node for telemetry labels.

    Returns:
        Decorator that instruments the node factory function.
    """
    def decorator(create_fn: Callable[..., NodeFunc]) -> Callable[..., NodeFunc]:
        @functools.wraps(create_fn)
        def wrapper(*args, **kwargs) -> NodeFunc:
            original_node = create_fn(*args, **kwargs)

            @functools.wraps(original_node)
            async def instrumented_node(state: ProgenitorState) -> ProgenitorState:
                tracer, metrics = _get_telemetry()
                start_time = time.perf_counter()
                success = False
                error_type = None

                span_attrs = {
                    "node.name": node_name,
                    "state.cycle_count": state.get("cycle_count", 0),
                    "state.has_input": state.get("current_input") is not None,
                }

                try:
                    with _trace_span(tracer, f"node.{node_name}", span_attrs) as span:
                        result = await original_node(state)
                        success = True

                        # Add result info to span
                        if span and isinstance(result, dict):
                            span.set_attribute("result.keys", str(list(result.keys())))

                        return result

                except Exception as e:
                    error_type = type(e).__name__

                    # Record exception in span
                    if tracer:
                        try:
                            with _trace_span(tracer, f"node.{node_name}.error") as error_span:
                                if error_span:
                                    error_span.record_exception(e)
                        except (AttributeError, TypeError) as te:
                            logger.debug("Failed to record exception in span: %s", te)

                    raise

                finally:
                    duration = time.perf_counter() - start_time

                    # Record metrics
                    if metrics:
                        try:
                            metrics.record_histogram(
                                "node_duration_seconds",
                                duration,
                                {"node": node_name}
                            )
                            metrics.increment(
                                "node_invocations_total",
                                {"node": node_name, "success": str(success).lower()}
                            )
                            if error_type:
                                metrics.increment(
                                    "node_errors_total",
                                    {"node": node_name, "error_type": error_type}
                                )
                        except Exception as e:
                            logger.debug("Failed to record metrics: %s", e)

            return instrumented_node
        return wrapper
    return decorator


def traced_method(method_name: str, span_name: Optional[str] = None):
    """Decorator for tracing individual async methods.

    Useful for instrumenting methods within classes without
    modifying the factory pattern.

    Usage:
        class MyService:
            @traced_method("my_operation")
            async def process(self, data: dict) -> dict:
                return {"result": "processed"}

    Args:
        method_name: Name for metrics labels.
        span_name: Optional custom span name (defaults to f"method.{method_name}").
    """
    def decorator(method: T) -> T:
        @functools.wraps(method)
        async def wrapper(*args, **kwargs):
            tracer, metrics = _get_telemetry()
            start_time = time.perf_counter()
            success = False

            actual_span_name = span_name or f"method.{method_name}"

            try:
                with _trace_span(tracer, actual_span_name) as span:
                    result = await method(*args, **kwargs)
                    success = True
                    return result

            finally:
                duration = time.perf_counter() - start_time
                if metrics:
                    try:
                        metrics.record_histogram(
                            "method_duration_seconds",
                            duration,
                            {"method": method_name}
                        )
                        metrics.increment(
                            "method_calls_total",
                            {"method": method_name, "success": str(success).lower()}
                        )
                    except (AttributeError, TypeError) as e:
                        logger.debug("Failed to record method metrics: %s", e)

        return wrapper  # type: ignore
    return decorator


def timed(metric_name: str, labels: Optional[dict] = None):
    """Simple timing decorator for any async function.

    Usage:
        @timed("llm_call", {"model": "gpt-4"})
        async def call_llm(prompt: str) -> str:
            return await client.complete(prompt)
    """
    def decorator(func: T) -> T:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            _, metrics = _get_telemetry()
            start_time = time.perf_counter()

            try:
                return await func(*args, **kwargs)
            finally:
                if metrics:
                    try:
                        metrics.record_histogram(
                            metric_name,
                            time.perf_counter() - start_time,
                            labels or {}
                        )
                    except (AttributeError, TypeError) as e:
                        logger.debug("Failed to record timing metric: %s", e)

        return wrapper  # type: ignore
    return decorator


class NodeInstrumentationMixin:
    """Mixin for classes that need standardized telemetry methods.

    Provides common telemetry helpers that can be used by node implementations.

    Usage:
        class MyNodeFactory(NodeInstrumentationMixin):
            def create_node(self):
                async def my_node(state):
                    with self.trace_operation("phase1"):
                        # do phase 1
                        pass
                    return state
    """

    @contextmanager
    def trace_operation(self, operation_name: str, attributes: Optional[dict] = None):
        """Context manager for tracing an operation within a node."""
        tracer, _ = _get_telemetry()

        if tracer is None:
            yield None
            return

        try:
            span = tracer.start_span(operation_name, attributes=attributes or {})
            yield span
            tracer.end_span(span)
        except (AttributeError, TypeError) as e:
            logger.debug("Trace operation failed: %s", e)
            yield None

    def record_metric(
        self,
        metric_type: str,
        name: str,
        value: float = 1.0,
        labels: Optional[dict] = None
    ):
        """Record a metric value."""
        _, metrics = _get_telemetry()
        if metrics is None:
            return

        try:
            if metric_type == "counter":
                metrics.increment(name, labels or {})
            elif metric_type == "histogram":
                metrics.record_histogram(name, value, labels or {})
        except Exception as e:
            logger.debug("Failed to record metric %s: %s", name, e)
