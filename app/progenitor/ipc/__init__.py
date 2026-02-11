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

from .client import IPCClient
from .protocol import (
    IPCMessage,
    MessageType,
    agent_event,
    decode_message,
    encode_message,
    read_message,
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
    "IPCServer",
    "IPCClient",
]
