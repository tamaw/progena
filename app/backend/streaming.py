"""
Streaming Callbacks Module - Transform blocking functions into lazy iterators.

Based on patterns from text-generation-webui for real-time token streaming.

Features:
- Iteratorize class converts callback-based APIs to async generators
- Queue-based communication between threads
- Graceful stop mechanism via StopNowException
- Sentinel-based iteration termination
- Thread-safe stop signaling
"""

import asyncio
import logging
import traceback
from queue import Queue, Empty
from threading import Thread, Event
from typing import Any, Callable, Iterator, Optional, TypeVar, Generic, cast
from dataclasses import dataclass

logger = logging.getLogger(__name__)


class StopNowException(Exception):
    """Exception to signal immediate stop of generation."""
    pass


# Global stop flag for emergency stops
_stop_everything = Event()


def stop_everything() -> None:
    """Signal all generators to stop immediately."""
    _stop_everything.set()


def reset_stop_flag() -> None:
    """Reset the global stop flag."""
    _stop_everything.clear()


def is_stop_requested() -> bool:
    """Check if stop has been requested."""
    return _stop_everything.is_set()


T = TypeVar('T')


class Iteratorize(Generic[T]):
    """
    Transforms a function that takes a callback into a lazy iterator (generator).

    Adapted from text-generation-webui: https://stackoverflow.com/a/9969000

    This is useful for converting callback-based streaming APIs (like many LLM
    libraries) into Python iterators that can be consumed with for loops or
    converted to async generators.

    Example:
        def generate_with_callback(prompt, callback):
            for token in llm.generate(prompt):
                callback(token)
            return "done"

        for token in Iteratorize(generate_with_callback, args=["Hello"]):
            print(token, end="", flush=True)
    """

    def __init__(
        self,
        func: Callable[..., Any],
        args: Optional[list] = None,
        kwargs: Optional[dict] = None,
        callback: Optional[Callable[[Any], None]] = None,
        sentinel: Optional[Any] = None,
    ):
        """
        Initialize the iterator.

        Args:
            func: Function that accepts a 'callback' keyword argument
            args: Positional arguments to pass to func
            kwargs: Keyword arguments to pass to func
            callback: Optional callback to invoke when func returns
            sentinel: Custom sentinel object (defaults to new object())
        """
        self.func = func
        self.completion_callback = callback
        self.queue: Queue[T] = Queue()
        self.sentinel = sentinel if sentinel is not None else object()
        self.args = args or []
        self.kwargs = kwargs or {}
        self.stop_now = False
        self._result: Any = None
        self._exception: Optional[Exception] = None

        def _callback(val: T) -> None:
            """Internal callback that puts values on the queue."""
            if self.stop_now or is_stop_requested():
                raise StopNowException("Generation stopped by user request")
            self.queue.put(val)

        def _gen_task() -> None:
            """Background thread task that runs the generator function."""
            try:
                self._result = self.func(
                    *self.args,
                    callback=_callback,
                    **self.kwargs
                )
            except StopNowException:
                logger.debug("Generation stopped by StopNowException")
            except Exception as e:
                logger.error("Error in generator task: %s", e)
                traceback.print_exc()
                self._exception = e
            finally:
                self.queue.put(cast(T, self.sentinel))
                if self.completion_callback:
                    try:
                        self.completion_callback(self._result)
                    except Exception as e:
                        logger.error("Error in completion callback: %s", e)

        self.thread = Thread(target=_gen_task, daemon=True)
        self.thread.start()

    def __iter__(self) -> Iterator[T]:
        return self

    def __next__(self) -> T:
        obj = self.queue.get(block=True, timeout=None)
        if obj is self.sentinel:
            if self._exception:
                raise self._exception
            raise StopIteration
        return obj

    def __del__(self) -> None:
        """Cleanup when iterator is garbage collected."""
        self.stop_now = True

    def __enter__(self) -> "Iteratorize[T]":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.stop_now = True
        # Give the thread a moment to notice and stop
        self.thread.join(timeout=0.5)

    def stop(self) -> None:
        """Request the generator to stop."""
        self.stop_now = True

    @property
    def result(self) -> Any:
        """Get the result returned by the wrapped function."""
        return self._result


@dataclass
class StreamChunk:
    """A chunk of streamed content with metadata."""
    content: str
    index: int
    is_final: bool = False
    metadata: Optional[dict[str, Any]] = None


class AsyncIteratorize(Generic[T]):
    """
    Async version of Iteratorize for use with async generators.

    Converts a callback-based function into an async iterator.
    """

    def __init__(
        self,
        func: Callable[..., Any],
        args: Optional[list] = None,
        kwargs: Optional[dict] = None,
        sentinel: Optional[Any] = None,
    ):
        self.func = func
        self.queue: asyncio.Queue[T] = asyncio.Queue()
        self.sentinel = sentinel if sentinel is not None else object()
        self.args = args or []
        self.kwargs = kwargs or {}
        self.stop_now = False
        self._result: Any = None
        self._exception: Optional[Exception] = None
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """Start the background task."""
        async def _callback(val: T) -> None:
            if self.stop_now or is_stop_requested():
                raise StopNowException()
            await self.queue.put(val)

        async def _gen_task() -> None:
            try:
                # Handle both sync and async functions
                if asyncio.iscoroutinefunction(self.func):
                    self._result = await self.func(
                        *self.args,
                        callback=_callback,
                        **self.kwargs
                    )
                else:
                    # Run sync function in executor
                    def sync_wrapper():
                        def sync_callback(val):
                            asyncio.run_coroutine_threadsafe(
                                self.queue.put(val),
                                asyncio.get_event_loop()
                            )
                        return self.func(
                            *self.args,
                            callback=sync_callback,
                            **self.kwargs
                        )
                    loop = asyncio.get_event_loop()
                    self._result = await loop.run_in_executor(None, sync_wrapper)
            except StopNowException:
                logger.debug("Async generation stopped")
            except Exception as e:
                logger.error("Error in async generator: %s", e)
                self._exception = e
            finally:
                await self.queue.put(cast(T, self.sentinel))

        self._task = asyncio.create_task(_gen_task())

    def __aiter__(self):
        return self

    async def __anext__(self) -> T:
        if self._task is None:
            await self.start()

        obj = await self.queue.get()
        if obj is self.sentinel:
            if self._exception:
                raise self._exception
            raise StopAsyncIteration
        return obj

    async def stop(self) -> None:
        """Stop the generator."""
        self.stop_now = True
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass


class StreamingBuffer:
    """
    Buffer for accumulating and processing streaming chunks.

    Useful for batching small chunks or detecting patterns in the stream.
    """

    def __init__(self, flush_threshold: int = 100, flush_timeout: float = 0.1):
        """
        Initialize the streaming buffer.

        Args:
            flush_threshold: Number of characters before auto-flush
            flush_timeout: Seconds to wait before flushing partial buffer
        """
        self._buffer: list[str] = []
        self._total_length = 0
        self.flush_threshold = flush_threshold
        self.flush_timeout = flush_timeout
        self._last_flush = asyncio.get_event_loop().time() if asyncio.get_event_loop().is_running() else 0

    def add(self, chunk: str) -> Optional[str]:
        """
        Add a chunk to the buffer.

        Returns:
            Flushed content if threshold reached, else None
        """
        self._buffer.append(chunk)
        self._total_length += len(chunk)

        if self._total_length >= self.flush_threshold:
            return self.flush()
        return None

    def flush(self) -> str:
        """Flush and return buffered content."""
        content = "".join(self._buffer)
        self._buffer.clear()
        self._total_length = 0
        return content

    def should_flush(self, current_time: float) -> bool:
        """Check if buffer should be flushed based on timeout."""
        if not self._buffer:
            return False
        return (current_time - self._last_flush) >= self.flush_timeout

    @property
    def has_content(self) -> bool:
        """Check if buffer has any content."""
        return self._total_length > 0


async def stream_with_callback(
    async_gen,
    on_chunk: Optional[Callable[[str, int], None]] = None,
    on_complete: Optional[Callable[[str], None]] = None,
    on_error: Optional[Callable[[Exception], None]] = None,
) -> str:
    """
    Consume an async generator with callbacks for each event.

    Args:
        async_gen: Async generator yielding string chunks
        on_chunk: Called with (chunk, index) for each chunk
        on_complete: Called with full response when done
        on_error: Called with exception if error occurs

    Returns:
        The complete accumulated response
    """
    chunks: list[str] = []
    index = 0

    try:
        async for chunk in async_gen:
            chunks.append(chunk)
            if on_chunk:
                on_chunk(chunk, index)
            index += 1

        full_response = "".join(chunks)
        if on_complete:
            on_complete(full_response)
        return full_response

    except Exception as e:
        if on_error:
            on_error(e)
        raise
