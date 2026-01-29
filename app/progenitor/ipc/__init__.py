"""
IPC (Inter-Process Communication) module for Progenitor.

Provides socket-based communication between the chat_server and Progenitor agent,
replacing the unreliable stdin/stdout mechanism.

Features:
- TCP socket-based communication (works on all platforms)
- Message framing with length-prefix protocol
- Native streaming support
- Heartbeat/keepalive mechanism
- Automatic reconnection
"""

from .protocol import (
    MessageType,
    IPCMessage,
    encode_message,
    decode_message,
    read_message,
    write_message,
)
from .server import IPCServer
from .client import IPCClient

__all__ = [
    "MessageType",
    "IPCMessage",
    "encode_message",
    "decode_message",
    "read_message",
    "write_message",
    "IPCServer",
    "IPCClient",
]
