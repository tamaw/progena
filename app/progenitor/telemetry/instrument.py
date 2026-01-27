"""
Telemetry Instrumentation - Auto-instrument Progenitor components.

Provides decorators and utilities to instrument:
- Graph nodes
- LLM calls
- Tool calls
- Sub-agent execution
- Loop cycles
"""

import functools
import time
import logging
import asyncio
from typing import Any, Callable, Optional, TypeVar, ParamSpec, Awaitable, Union, cast

from .metrics import get_metrics, Timer
from .tracer import get_tracer, Span, SpanKind, SpanStatus, SpanNames
from .context import TelemetryContext, get_current_span

logger = logging.getLogger(__name__)

P = ParamSpec("P")
T = TypeVar("T")

# Type alias for async functions
AsyncFunc = Callable[P, Awaitable[T]]


def instrument_node(node_name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """
    Decorator to instrument a graph node function.

    Tracks:
    - Execution time
    - Success/failure
    - Input/output state keys
    """
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            tracer = get_tracer()
            metrics = get_metrics()

            span_name = SpanNames.NODE_EXECUTE.format(name=node_name)

            async with tracer.aspan(span_name, SpanKind.INTERNAL) as span:
                span.set_attribute("node.name", node_name)

                # Track input state keys if first arg is state dict
                if args and isinstance(args[0], dict):
                    span.set_attribute("node.input_keys", list(args[0].keys())[:20])

                start = time.perf_counter()

                try:
                    result = await func(*args, **kwargs)

                    # Track output keys
                    if isinstance(result, dict):
                        span.set_attribute("node.output_keys", list(result.keys()))

                    metrics.node_executions.inc()
                    return result

                except Exception as e:
                    span.record_exception(e)
                    span.set_attribute("node.error", str(e))
                    raise

                finally:
                    duration = time.perf_counter() - start
                    metrics.node_duration.observe(duration)
                    span.set_attribute("node.duration_ms", duration * 1000)

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            tracer = get_tracer()
            metrics = get_metrics()

            span_name = SpanNames.NODE_EXECUTE.format(name=node_name)

            with tracer.span(span_name, SpanKind.INTERNAL) as span:
                span.set_attribute("node.name", node_name)

                start = time.perf_counter()

                try:
                    result = func(*args, **kwargs)
                    metrics.node_executions.inc()
                    return result
                except Exception as e:
                    span.record_exception(e)
                    raise
                finally:
                    duration = time.perf_counter() - start
                    metrics.node_duration.observe(duration)

        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator


def instrument_tool_call(tool_name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """
    Decorator to instrument tool calls.

    Tracks:
    - Call duration
    - Success/failure
    - Tool arguments (sanitized)
    """
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            tracer = get_tracer()
            metrics = get_metrics()

            span_name = SpanNames.TOOL_CALL.format(name=tool_name)

            async with tracer.aspan(span_name, SpanKind.CLIENT) as span:
                span.set_attribute("tool.name", tool_name)
                span.set_attribute("tool.args_count", len(kwargs))

                start = time.perf_counter()

                try:
                    result = await func(*args, **kwargs)

                    span.set_status(SpanStatus.OK)
                    metrics.tool_calls_total.inc()

                    return result

                except Exception as e:
                    span.record_exception(e)
                    metrics.tool_call_errors.inc()
                    raise

                finally:
                    duration = time.perf_counter() - start
                    metrics.tool_call_duration.observe(duration)
                    span.set_attribute("tool.duration_ms", duration * 1000)

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            tracer = get_tracer()
            metrics = get_metrics()

            span_name = SpanNames.TOOL_CALL.format(name=tool_name)

            with tracer.span(span_name, SpanKind.CLIENT) as span:
                span.set_attribute("tool.name", tool_name)

                start = time.perf_counter()

                try:
                    result = func(*args, **kwargs)
                    span.set_status(SpanStatus.OK)
                    metrics.tool_calls_total.inc()
                    return result
                except Exception as e:
                    span.record_exception(e)
                    metrics.tool_call_errors.inc()
                    raise
                finally:
                    duration = time.perf_counter() - start
                    metrics.tool_call_duration.observe(duration)

        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator


def instrument_llm_call(
    model: Optional[str] = None,
    role: Optional[str] = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """
    Decorator to instrument LLM API calls.

    Tracks:
    - Call duration
    - Token usage (if available in response)
    - Model and role
    """
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            tracer = get_tracer()
            metrics = get_metrics()

            async with tracer.aspan(SpanNames.LLM_CALL, SpanKind.CLIENT) as span:
                if model:
                    span.set_attribute("llm.model", model)
                if role:
                    span.set_attribute("llm.role", role)

                # Try to get model from kwargs
                actual_model = kwargs.get("model") or model
                if actual_model:
                    span.set_attribute("llm.model", actual_model)

                start = time.perf_counter()

                try:
                    result = await func(*args, **kwargs)

                    # Try to extract token usage from response
                    if hasattr(result, "usage"):
                        usage = result.usage
                        if hasattr(usage, "prompt_tokens"):
                            metrics.llm_tokens_prompt.inc(usage.prompt_tokens)
                            span.set_attribute("llm.prompt_tokens", usage.prompt_tokens)
                        if hasattr(usage, "completion_tokens"):
                            metrics.llm_tokens_completion.inc(usage.completion_tokens)
                            span.set_attribute("llm.completion_tokens", usage.completion_tokens)

                    span.set_status(SpanStatus.OK)
                    metrics.llm_calls_total.inc()

                    return result

                except Exception as e:
                    span.record_exception(e)
                    raise

                finally:
                    duration = time.perf_counter() - start
                    metrics.llm_call_duration.observe(duration)
                    span.set_attribute("llm.duration_ms", duration * 1000)

        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return func  # Sync LLM calls are rare, skip instrumentation

    return decorator


class AgentTelemetry:
    """
    Telemetry helper for sub-agent tracking.

    Provides methods to record agent lifecycle events.
    """

    def __init__(self, agent_name: str, agent_id: str):
        self.agent_name = agent_name
        self.agent_id = agent_id
        self.tracer = get_tracer()
        self.metrics = get_metrics()
        self._span: Optional[Span] = None
        self._start_time: Optional[float] = None

    def start(self, parent_context: Optional[TelemetryContext] = None) -> Span:
        """Record agent spawn and start tracking."""
        self._span = self.tracer.start_span(
            SpanNames.AGENT_EXECUTE,
            kind=SpanKind.INTERNAL,
            attributes={
                "agent.name": self.agent_name,
                "agent.id": self.agent_id,
            },
            context=parent_context,
        )
        self._start_time = time.perf_counter()

        self.metrics.agent_spawned.inc()
        self.metrics.active_agents.inc()

        return self._span

    def add_event(self, name: str, attributes: Optional[dict[str, Any]] = None) -> None:
        """Add an event to the agent span."""
        if self._span:
            self._span.add_event(name, attributes)

    def set_attribute(self, key: str, value: Any) -> None:
        """Set attribute on agent span."""
        if self._span:
            self._span.set_attribute(key, value)

    def complete(self, success: bool = True, error: Optional[str] = None) -> None:
        """Record agent completion."""
        if self._span:
            if success:
                self._span.set_status(SpanStatus.OK)
                self.metrics.agent_completed.inc()
            else:
                self._span.set_status(SpanStatus.ERROR, error)
                self.metrics.agent_failed.inc()

            if self._start_time:
                duration = time.perf_counter() - self._start_time
                self.metrics.agent_duration.observe(duration)
                self._span.set_attribute("agent.duration_ms", duration * 1000)

            self.tracer.end_span(self._span)

        self.metrics.active_agents.dec()

    def get_context(self) -> TelemetryContext:
        """Get context for passing to child operations."""
        if self._span:
            return self._span.get_context()
        return TelemetryContext.current()


class IterationTelemetry:
    """
    Telemetry helper for tracking inner loop iterations.

    Records the full iteration cycle: observe → think → plan → build → execute → verify → learn
    """

    def __init__(self, iteration: int, work_item_id: Optional[str] = None):
        self.iteration = iteration
        self.work_item_id = work_item_id
        self.tracer = get_tracer()
        self.metrics = get_metrics()
        self._span: Optional[Span] = None
        self._phase_spans: dict[str, Span] = {}
        self._start_time: Optional[float] = None

    def start(self) -> Span:
        """Start tracking an iteration."""
        self._span = self.tracer.start_span(
            f"iteration.{self.iteration}",
            kind=SpanKind.INTERNAL,
            attributes={
                "iteration.number": self.iteration,
                "iteration.work_item_id": self.work_item_id,
            },
        )
        self._start_time = time.perf_counter()
        self.metrics.iterations_total.inc()

        return self._span

    def start_phase(self, phase: str) -> Span:
        """Start tracking a phase within the iteration."""
        phase_span = self.tracer.start_span(
            f"phase.{phase}",
            kind=SpanKind.INTERNAL,
            attributes={
                "phase.name": phase,
                "iteration.number": self.iteration,
            },
        )
        self._phase_spans[phase] = phase_span

        if self._span:
            self._span.add_event(f"phase.{phase}.start")

        return phase_span

    def end_phase(self, phase: str, success: bool = True) -> None:
        """End tracking a phase."""
        if phase in self._phase_spans:
            span = self._phase_spans[phase]
            if success:
                span.set_status(SpanStatus.OK)
            else:
                span.set_status(SpanStatus.ERROR)
            self.tracer.end_span(span)

            if self._span:
                self._span.add_event(f"phase.{phase}.end", {"success": success})

    def record_verification(
        self,
        score: float,
        passed: bool,
        gaps: Optional[list[str]] = None,
    ) -> None:
        """Record verification results."""
        self.metrics.verification_score.set(score)

        if passed:
            self.metrics.verification_passed.inc()
        else:
            self.metrics.verification_failed.inc()

        if self._span:
            self._span.set_attributes({
                "verification.score": score,
                "verification.passed": passed,
                "verification.gaps": gaps or [],
            })

    def complete(self, success: bool = True) -> None:
        """Complete iteration tracking."""
        if self._span:
            if success:
                self._span.set_status(SpanStatus.OK)
            else:
                self._span.set_status(SpanStatus.ERROR)

            if self._start_time:
                duration = time.perf_counter() - self._start_time
                self.metrics.iteration_duration.observe(duration)
                self._span.set_attribute("iteration.duration_ms", duration * 1000)

            self.tracer.end_span(self._span)


class CycleTelemetry:
    """
    Telemetry helper for loop cycles.

    Tracks the outer loop execution.
    """

    def __init__(self, cycle_number: int, run_id: str):
        self.cycle_number = cycle_number
        self.run_id = run_id
        self.tracer = get_tracer()
        self.metrics = get_metrics()
        self._span: Optional[Span] = None
        self._start_time: Optional[float] = None

    def start(self) -> Span:
        """Start tracking a cycle."""
        self._span = self.tracer.start_span(
            SpanNames.LOOP_CYCLE,
            kind=SpanKind.INTERNAL,
            attributes={
                "cycle.number": self.cycle_number,
                "cycle.run_id": self.run_id,
            },
        )
        self._start_time = time.perf_counter()
        self.metrics.cycle_count.inc()

        return self._span

    def add_event(self, name: str, attributes: Optional[dict[str, Any]] = None) -> None:
        """Add event to cycle span."""
        if self._span:
            self._span.add_event(name, attributes)

    def set_attribute(self, key: str, value: Any) -> None:
        """Set attribute on cycle span."""
        if self._span:
            self._span.set_attribute(key, value)

    def complete(self, success: bool = True, error: Optional[str] = None) -> None:
        """Complete cycle tracking."""
        if self._span:
            if success:
                self._span.set_status(SpanStatus.OK)
            else:
                self._span.set_status(SpanStatus.ERROR, error)

            if self._start_time:
                duration = time.perf_counter() - self._start_time
                self._span.set_attribute("cycle.duration_ms", duration * 1000)

            self.tracer.end_span(self._span)
