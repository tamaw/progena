"""
Circuit Breaker Pattern for LLM Calls.

FEAT-004: Prevent cascading failures from LLM issues.

The circuit breaker pattern prevents repeated calls to a failing service,
allowing it time to recover. States:
- CLOSED: Normal operation, calls pass through
- OPEN: Service is failing, calls are rejected immediately
- HALF_OPEN: Testing if service has recovered

Usage:
    breaker = CircuitBreaker(failure_threshold=5, reset_timeout=60.0)

    try:
        result = await breaker.call(llm_client.generate, prompt)
    except CircuitOpenError:
        # Use fallback behavior
        ...
"""

import asyncio
import inspect
import logging
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, TypeVar, cast

logger = logging.getLogger(__name__)

T = TypeVar("T")


class CircuitState(StrEnum):
    """Circuit breaker states."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half-open"


class CircuitOpenError(Exception):
    """Raised when circuit is open and calls are blocked."""

    def __init__(
        self,
        message: str = "Circuit breaker is open",
        reset_time: float | None = None,
    ):
        super().__init__(message)
        self.reset_time = reset_time


@dataclass
class CircuitBreaker:
    """Circuit breaker for protecting against cascading failures.

    Tracks failures and opens the circuit when threshold is reached,
    preventing further calls until a reset timeout expires.

    Attributes:
        failure_threshold: Number of failures before opening circuit.
        reset_timeout: Seconds to wait before trying again.
        half_open_max_calls: Max calls allowed in half-open state.
    """

    failure_threshold: int = 5
    reset_timeout: float = 60.0
    half_open_max_calls: int = 3

    # State tracking (managed internally)
    _failures: int = field(default=0, init=False)
    _state: CircuitState = field(default=CircuitState.CLOSED, init=False)
    _last_failure_time: float = field(default=0.0, init=False)
    _half_open_calls: int = field(default=0, init=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)

    @property
    def state(self) -> str:
        """Get current circuit state."""
        return self._state.value

    @property
    def failure_count(self) -> int:
        """Get current failure count."""
        return self._failures

    @property
    def is_open(self) -> bool:
        """Check if circuit is open (blocking calls)."""
        return self._state == CircuitState.OPEN

    @property
    def time_until_reset(self) -> float:
        """Get seconds until circuit resets (0 if not open)."""
        if self._state != CircuitState.OPEN:
            return 0.0

        elapsed = time.time() - self._last_failure_time
        remaining = self.reset_timeout - elapsed
        return max(0.0, remaining)

    async def call(
        self,
        func: Callable[..., T],
        *args: Any,
        **kwargs: Any,
    ) -> T:
        """Execute a function through the circuit breaker.

        Args:
            func: Async function to call.
            *args: Positional arguments for the function.
            **kwargs: Keyword arguments for the function.

        Returns:
            Result from the function.

        Raises:
            CircuitOpenError: If circuit is open.
        """
        async with self._lock:
            self._check_state_transition()

            if self._state == CircuitState.OPEN:
                raise CircuitOpenError(
                    f"Circuit breaker is open (failures={self._failures}, "
                    f"reset in {self.time_until_reset:.1f}s)",
                    reset_time=self.time_until_reset,
                )

            if self._state == CircuitState.HALF_OPEN:
                if self._half_open_calls >= self.half_open_max_calls:
                    # Too many half-open calls, stay closed
                    raise CircuitOpenError(
                        "Circuit breaker half-open limit reached",
                    )
                self._half_open_calls += 1

        try:
            if inspect.iscoroutinefunction(func):
                result = await func(*args, **kwargs)
            else:
                result = func(*args, **kwargs)

            # Success - record it
            await self._record_success()
            return cast(T, result)

        except Exception:
            await self._record_failure()
            raise

    async def _record_success(self) -> None:
        """Record a successful call."""
        async with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                # Reset circuit on success in half-open state
                logger.info("Circuit breaker reset to CLOSED after successful half-open call")
                self._state = CircuitState.CLOSED
                self._failures = 0
                self._half_open_calls = 0
            elif self._state == CircuitState.CLOSED:
                # Reduce failure count on success (gradual recovery)
                if self._failures > 0:
                    self._failures -= 1

    async def _record_failure(self) -> None:
        """Record a failed call."""
        async with self._lock:
            self._failures += 1
            self._last_failure_time = time.time()

            if self._state == CircuitState.HALF_OPEN:
                # Failure in half-open state reopens circuit
                logger.warning(
                    "Circuit breaker reopened after half-open failure (failures=%d)",
                    self._failures,
                )
                self._state = CircuitState.OPEN
                self._half_open_calls = 0

            elif self._state == CircuitState.CLOSED:
                if self._failures >= self.failure_threshold:
                    logger.error(
                        "Circuit breaker opened (failures=%d, threshold=%d)",
                        self._failures,
                        self.failure_threshold,
                    )
                    self._state = CircuitState.OPEN

    def _check_state_transition(self) -> None:
        """Check if state should transition based on time."""
        if self._state == CircuitState.OPEN:
            elapsed = time.time() - self._last_failure_time
            if elapsed >= self.reset_timeout:
                logger.info(
                    "Circuit breaker transitioning to HALF_OPEN after %.1fs timeout",
                    elapsed,
                )
                self._state = CircuitState.HALF_OPEN
                self._half_open_calls = 0

    def record_success(self) -> None:
        """Synchronous success recording (for sync code)."""
        self._schedule_sync_update(self._record_success)

    def record_failure(self) -> None:
        """Synchronous failure recording (for sync code)."""
        self._schedule_sync_update(self._record_failure)

    def _schedule_sync_update(self, update: Callable[[], Coroutine[Any, Any, None]]) -> None:
        """Run sync-side state updates through the same async locking path."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(update())
            return

        loop.create_task(update())

    def reset(self) -> None:
        """Manually reset the circuit breaker."""
        self._state = CircuitState.CLOSED
        self._failures = 0
        self._half_open_calls = 0
        self._last_failure_time = 0.0
        logger.info("Circuit breaker manually reset")

    def get_stats(self) -> dict[str, Any]:
        """Get circuit breaker statistics."""
        return {
            "state": self._state.value,
            "failures": self._failures,
            "failure_threshold": self.failure_threshold,
            "reset_timeout": self.reset_timeout,
            "time_until_reset": self.time_until_reset,
            "half_open_calls": self._half_open_calls,
        }

    async def __aenter__(self) -> "CircuitBreaker":
        """Async context manager entry - check if call is allowed."""
        async with self._lock:
            self._check_state_transition()

            if self._state == CircuitState.OPEN:
                raise CircuitOpenError(
                    f"Circuit breaker is open (failures={self._failures}, "
                    f"reset in {self.time_until_reset:.1f}s)",
                    reset_time=self.time_until_reset,
                )

            if self._state == CircuitState.HALF_OPEN:
                if self._half_open_calls >= self.half_open_max_calls:
                    raise CircuitOpenError(
                        "Circuit breaker half-open limit reached",
                    )
                self._half_open_calls += 1

        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> bool:
        """Async context manager exit - record success or failure."""
        if exc_type is None:
            await self._record_success()
        else:
            await self._record_failure()
        return False  # Don't suppress exceptions


# Global circuit breaker instance for LLM calls
_llm_circuit_breaker: CircuitBreaker | None = None


def get_llm_circuit_breaker(
    failure_threshold: int = 5,
    reset_timeout: float = 60.0,
) -> CircuitBreaker:
    """Get or create the global LLM circuit breaker.

    Args:
        failure_threshold: Failures before opening circuit.
        reset_timeout: Seconds before attempting recovery.

    Returns:
        CircuitBreaker instance.
    """
    global _llm_circuit_breaker
    if _llm_circuit_breaker is None:
        _llm_circuit_breaker = CircuitBreaker(
            failure_threshold=failure_threshold,
            reset_timeout=reset_timeout,
        )
    return _llm_circuit_breaker


def reset_llm_circuit_breaker() -> None:
    """Reset the global LLM circuit breaker."""
    global _llm_circuit_breaker
    if _llm_circuit_breaker:
        _llm_circuit_breaker.reset()
