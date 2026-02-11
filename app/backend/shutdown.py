"""
Graceful Shutdown Module - Clean resource cleanup on shutdown.

Based on patterns from text-generation-webui for graceful shutdown handling.

Features:
- Signal handling (SIGINT, SIGTERM)
- Request draining with timeout
- Resource cleanup callbacks
- Model/service stop before exit
- Observability data flushing
- Shutdown status tracking
"""

import asyncio
import logging
import signal
import time
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)


class ShutdownPhase(StrEnum):
    """Phases of graceful shutdown."""
    RUNNING = "running"
    DRAINING = "draining"  # Stopping new requests, waiting for active ones
    STOPPING_SERVICES = "stopping_services"
    CLEANUP = "cleanup"
    COMPLETED = "completed"


@dataclass
class ShutdownCallback:
    """A registered shutdown callback."""
    name: str
    callback: Callable[[], Awaitable[None]]
    priority: int = 100  # Lower = earlier execution
    timeout: float = 10.0  # Timeout for this callback


@dataclass
class ShutdownStats:
    """Statistics about shutdown process."""
    initiated_at: datetime | None = None
    completed_at: datetime | None = None
    drain_started_at: datetime | None = None
    requests_drained: int = 0
    requests_timed_out: int = 0
    callbacks_executed: int = 0
    callbacks_failed: int = 0
    total_duration_ms: float | None = None


class GracefulShutdownManager:
    """
    Comprehensive graceful shutdown management.

    Provides:
    - Multi-phase shutdown with timeouts
    - Request draining
    - Callback registration for cleanup
    - Signal handling
    - Detailed shutdown statistics
    """

    def __init__(
        self,
        drain_timeout: float = 30.0,
        total_timeout: float = 60.0,
    ):
        """
        Initialize shutdown manager.

        Args:
            drain_timeout: Max seconds to wait for active requests to drain
            total_timeout: Max total seconds for entire shutdown process
        """
        self.drain_timeout = drain_timeout
        self.total_timeout = total_timeout

        self._phase = ShutdownPhase.RUNNING
        self._active_requests = 0
        self._shutdown_event: asyncio.Event | None = None
        self._drained_event: asyncio.Event | None = None
        self._lock = asyncio.Lock()
        self._callbacks: list[ShutdownCallback] = []
        self._drain_callbacks: list[ShutdownCallback] = []  # Callbacks to run when draining starts
        self._stats = ShutdownStats()

        # Signal handling
        self._original_handlers: dict[int, Any] = {}
        self._signals_installed = False

    @property
    def phase(self) -> ShutdownPhase:
        """Get current shutdown phase."""
        return self._phase

    @property
    def is_shutting_down(self) -> bool:
        """Check if shutdown has been initiated."""
        return self._phase != ShutdownPhase.RUNNING

    @property
    def is_accepting_requests(self) -> bool:
        """Check if new requests should be accepted."""
        return self._phase == ShutdownPhase.RUNNING

    @property
    def active_requests(self) -> int:
        """Get number of active requests."""
        return self._active_requests

    @property
    def stats(self) -> ShutdownStats:
        """Get shutdown statistics."""
        return self._stats

    def register_callback(
        self,
        name: str,
        callback: Callable[[], Awaitable[None]],
        priority: int = 100,
        timeout: float = 10.0,
    ) -> None:
        """
        Register a shutdown callback.

        Lower priority values execute first.

        Args:
            name: Descriptive name for logging
            callback: Async function to call during shutdown
            priority: Execution order (lower = earlier)
            timeout: Max seconds to wait for this callback
        """
        self._callbacks.append(ShutdownCallback(
            name=name,
            callback=callback,
            priority=priority,
            timeout=timeout,
        ))
        self._callbacks.sort(key=lambda c: c.priority)
        logger.debug("Registered shutdown callback: %s (priority=%d)", name, priority)

    def register_drain_callback(
        self,
        name: str,
        callback: Callable[[], Awaitable[None]],
        priority: int = 100,
        timeout: float = 10.0,
    ) -> None:
        """
        Register a callback to run when draining starts.

        These callbacks run at the beginning of the drain phase to help
        close long-running connections (like SSE) that would otherwise
        block draining.

        Lower priority values execute first.

        Args:
            name: Descriptive name for logging
            callback: Async function to call when draining starts
            priority: Execution order (lower = earlier)
            timeout: Max seconds to wait for this callback
        """
        self._drain_callbacks.append(ShutdownCallback(
            name=name,
            callback=callback,
            priority=priority,
            timeout=timeout,
        ))
        self._drain_callbacks.sort(key=lambda c: c.priority)
        logger.debug("Registered drain callback: %s (priority=%d)", name, priority)

    def install_signal_handlers(self) -> None:
        """Install signal handlers for graceful shutdown."""
        if self._signals_installed:
            return

        def _signal_handler(signum: int, frame) -> None:
            sig_name = signal.Signals(signum).name
            logger.info("Received %s signal, initiating graceful shutdown", sig_name)

            # Schedule shutdown in the event loop
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self.initiate_shutdown())
            except RuntimeError:
                # No running loop, just set the flag
                self._phase = ShutdownPhase.DRAINING

        # Store original handlers
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                self._original_handlers[sig] = signal.signal(sig, _signal_handler)
            except (ValueError, OSError) as e:
                # Can't set handler (e.g., not main thread)
                logger.debug("Could not install handler for %s: %s", sig, e)

        self._signals_installed = True
        logger.debug("Signal handlers installed")

    def restore_signal_handlers(self) -> None:
        """Restore original signal handlers."""
        for sig, handler in self._original_handlers.items():
            with suppress(ValueError, OSError):
                signal.signal(sig, handler)
        self._original_handlers.clear()
        self._signals_installed = False

    async def start_request(self) -> bool:
        """
        Start tracking a request.

        Returns:
            True if request is allowed, False if shutting down
        """
        async with self._lock:
            if not self.is_accepting_requests:
                return False
            self._active_requests += 1
            return True

    async def end_request(self) -> None:
        """Mark a request as complete."""
        async with self._lock:
            self._active_requests = max(0, self._active_requests - 1)
            self._stats.requests_drained += 1

            # Signal if we're draining and all requests complete
            if (
                self._phase == ShutdownPhase.DRAINING
                and self._active_requests == 0
                and self._drained_event
            ):
                self._drained_event.set()

    async def initiate_shutdown(self) -> None:
        """
        Initiate graceful shutdown process.

        This method orchestrates the entire shutdown sequence:
        1. Stop accepting new requests, run drain callbacks
        2. Drain active requests
        3. Execute shutdown callbacks
        4. Final cleanup
        """
        async with self._lock:
            if self._phase != ShutdownPhase.RUNNING:
                logger.debug("Shutdown already in progress (phase=%s)", self._phase)
                return

            self._phase = ShutdownPhase.DRAINING
            self._stats.initiated_at = datetime.now(UTC)
            self._stats.drain_started_at = datetime.now(UTC)
            self._shutdown_event = asyncio.Event()
            self._drained_event = asyncio.Event()

            if self._active_requests == 0:
                self._drained_event.set()

        logger.info(
            "Graceful shutdown initiated (active_requests=%d, drain_timeout=%.1fs)",
            self._active_requests,
            self.drain_timeout,
        )

        start_time = time.monotonic()

        # Execute drain callbacks first (e.g., close SSE connections)
        # These run before waiting for drain to help connections close gracefully
        await self._execute_drain_callbacks()

        # Phase 1: Drain requests
        try:
            await asyncio.wait_for(
                self._drained_event.wait(),
                timeout=self.drain_timeout,
            )
            logger.info("All requests drained successfully")
        except TimeoutError:
            self._stats.requests_timed_out = self._active_requests
            logger.warning(
                "Drain timeout reached with %d requests still active",
                self._active_requests,
            )

        # Phase 2: Stop services
        self._phase = ShutdownPhase.STOPPING_SERVICES
        await self._execute_callbacks()

        # Phase 3: Cleanup
        self._phase = ShutdownPhase.CLEANUP
        self.restore_signal_handlers()

        # Complete
        self._phase = ShutdownPhase.COMPLETED
        self._stats.completed_at = datetime.now(UTC)
        self._stats.total_duration_ms = (time.monotonic() - start_time) * 1000

        if self._shutdown_event:
            self._shutdown_event.set()

        logger.info(
            "Graceful shutdown complete (duration=%.1fms, drained=%d, timed_out=%d)",
            self._stats.total_duration_ms,
            self._stats.requests_drained,
            self._stats.requests_timed_out,
        )

    async def _execute_drain_callbacks(self) -> None:
        """Execute drain callbacks to help connections close gracefully."""
        if not self._drain_callbacks:
            return

        logger.info("Executing %d drain callbacks to close connections", len(self._drain_callbacks))
        for cb in self._drain_callbacks:
            logger.debug("Executing drain callback: %s", cb.name)
            try:
                await asyncio.wait_for(
                    cb.callback(),
                    timeout=cb.timeout,
                )
                logger.debug("Drain callback completed: %s", cb.name)
            except TimeoutError:
                logger.warning("Drain callback timed out: %s", cb.name)
            except Exception as e:
                logger.error("Drain callback failed: %s - %s", cb.name, e)

    async def _execute_callbacks(self) -> None:
        """Execute registered shutdown callbacks."""
        for cb in self._callbacks:
            logger.debug("Executing shutdown callback: %s", cb.name)
            try:
                await asyncio.wait_for(
                    cb.callback(),
                    timeout=cb.timeout,
                )
                self._stats.callbacks_executed += 1
                logger.debug("Callback completed: %s", cb.name)
            except TimeoutError:
                self._stats.callbacks_failed += 1
                logger.warning("Callback timed out: %s", cb.name)
            except Exception as e:
                self._stats.callbacks_failed += 1
                logger.error("Callback failed: %s - %s", cb.name, e)

    async def wait_for_shutdown(self) -> None:
        """Wait for shutdown to complete."""
        if self._shutdown_event:
            await self._shutdown_event.wait()

    def get_status(self) -> dict[str, Any]:
        """Get detailed shutdown status."""
        return {
            "phase": self._phase.value,
            "is_shutting_down": self.is_shutting_down,
            "is_accepting_requests": self.is_accepting_requests,
            "active_requests": self._active_requests,
            "drain_timeout_seconds": self.drain_timeout,
            "total_timeout_seconds": self.total_timeout,
            "registered_callbacks": len(self._callbacks),
            "stats": {
                "initiated_at": self._stats.initiated_at.isoformat() if self._stats.initiated_at else None,
                "completed_at": self._stats.completed_at.isoformat() if self._stats.completed_at else None,
                "requests_drained": self._stats.requests_drained,
                "requests_timed_out": self._stats.requests_timed_out,
                "callbacks_executed": self._stats.callbacks_executed,
                "callbacks_failed": self._stats.callbacks_failed,
                "total_duration_ms": self._stats.total_duration_ms,
            },
        }


@asynccontextmanager
async def managed_shutdown(
    shutdown_manager: GracefulShutdownManager,
):
    """
    Context manager for graceful shutdown handling.

    Usage:
        shutdown_manager = GracefulShutdownManager()

        async with managed_shutdown(shutdown_manager):
            # Your application runs here
            await run_server()
        # Shutdown automatically handled on exit
    """
    shutdown_manager.install_signal_handlers()
    try:
        yield shutdown_manager
    finally:
        if not shutdown_manager.is_shutting_down:
            await shutdown_manager.initiate_shutdown()
        else:
            await shutdown_manager.wait_for_shutdown()


# Convenience functions for common cleanup tasks

async def stop_client_safely(
    client,
    timeout: float = 10.0,
    name: str = "client",
) -> bool:
    """Safely stop a client with timeout."""
    if not client:
        return True

    logger.debug("Stopping %s...", name)
    try:
        stop_method = getattr(client, "stop", None) or getattr(client, "close", None)
        if stop_method:
            if asyncio.iscoroutinefunction(stop_method):
                await asyncio.wait_for(stop_method(), timeout=timeout)
            else:
                stop_method()
        logger.debug("%s stopped", name)
        return True
    except TimeoutError:
        logger.warning("%s stop timed out", name)
        return False
    except Exception as e:
        logger.error("Error stopping %s: %s", name, e)
        return False


async def commit_state_safely(
    state_manager,
    timeout: float = 5.0,
) -> bool:
    """Safely commit state with timeout."""
    if not state_manager:
        return True

    logger.debug("Committing state...")
    try:
        commit_method = getattr(state_manager, "commit_state", None)
        if commit_method:
            if asyncio.iscoroutinefunction(commit_method):
                await asyncio.wait_for(commit_method(), timeout=timeout)
            else:
                commit_method()
        logger.debug("State committed")
        return True
    except TimeoutError:
        logger.warning("State commit timed out")
        return False
    except Exception as e:
        logger.error("Error committing state: %s", e)
        return False


# Export for module use
__all__ = [
    "ShutdownPhase",
    "ShutdownCallback",
    "ShutdownStats",
    "GracefulShutdownManager",
    "managed_shutdown",
    "stop_client_safely",
    "commit_state_safely",
]
