"""
IPC (Inter-Process Communication) module for Progenitor.

Provides socket-based communication between the backend and Progenitor agent,
replacing the unreliable stdin/stdout mechanism.

Features:
- TCP socket-based communication (works on all platforms)
- Message framing with length-prefix protocol
- Native streaming support
- Heartbeat/keepalive mechanism
- Automatic reconnection
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .client import IPCClient
    from .protocol import (
        IPCMessage,
        MessageType,
        agent_event,
        decode_message,
        encode_message,
        read_message,
        stream_event,
        write_message,
    )
    from .server import IPCServer

__all__ = [
    "MessageType",
    "IPCMessage",
    "encode_message",
    "decode_message",
    "read_message",
    "write_message",
    "agent_event",
    "stream_event",
    "IPCClient",
    "IPCServer",
]


def __getattr__(name: str):
    if name == "IPCClient":
        from .client import IPCClient

        return IPCClient
    if name == "IPCServer":
        from .server import IPCServer

        return IPCServer
    if name in {
        "IPCMessage",
        "MessageType",
        "agent_event",
        "decode_message",
        "encode_message",
        "read_message",
        "stream_event",
        "write_message",
    }:
        from . import protocol

        return getattr(protocol, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
