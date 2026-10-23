"""Runtime components for Progena process infrastructure."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.progena.runtime.http.server import ProgenaHTTPServer

    from .progena import Progena
    from .queues import QueuedInput, RuntimeQueues

# Note: ProgenaLoop not imported here to avoid circular dependency
# Import directly: from .runtime.heartbeat import ProgenaLoop


def __getattr__(name: str):
    if name == "Progena":
        from .progena import Progena

        return Progena
    if name == "ProgenaHTTPServer":
        from src.progena.runtime.http.server import ProgenaHTTPServer

        return ProgenaHTTPServer
    if name == "QueuedInput":
        from .queues import QueuedInput

        return QueuedInput
    if name == "RuntimeQueues":
        from .queues import RuntimeQueues

        return RuntimeQueues
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "Progena",
    "ProgenaHTTPServer",
    "QueuedInput",
    "RuntimeQueues",
]
