"""Progenitor graph builders."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .heartbeat_graph import create_runtime_heartbeat_graph
    from .progenitor_graph import create_conversation_graph


def __getattr__(name: str):
    if name == "create_conversation_graph":
        from .progenitor_graph import create_conversation_graph

        return create_conversation_graph
    if name == "create_runtime_heartbeat_graph":
        from .heartbeat_graph import create_runtime_heartbeat_graph

        return create_runtime_heartbeat_graph
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "create_conversation_graph",
    "create_runtime_heartbeat_graph",
]
