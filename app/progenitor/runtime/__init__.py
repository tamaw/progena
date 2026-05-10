"""Runtime components for Progenitor process infrastructure."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .http import ProgenitorHTTPServer
    from .progenitor import Progenitor
    from .queues import QueuedInput, RuntimeQueues

# Note: ProgenitorLoop not imported here to avoid circular dependency
# Import directly: from .runtime.heartbeat import ProgenitorLoop


def __getattr__(name: str):
    if name == "Progenitor":
        from .progenitor import Progenitor

        return Progenitor
    if name == "ProgenitorHTTPServer":
        from .http import ProgenitorHTTPServer

        return ProgenitorHTTPServer
    if name == "QueuedInput":
        from .queues import QueuedInput

        return QueuedInput
    if name == "RuntimeQueues":
        from .queues import RuntimeQueues

        return RuntimeQueues
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "Progenitor",
    "ProgenitorHTTPServer",
    "QueuedInput",
    "RuntimeQueues",
]
