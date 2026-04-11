"""
IPC Message Protocol for Progenitor.

Defines the wire format for communication between backend and Progenitor.
Uses a simple length-prefixed JSON protocol for reliability.

Wire Format:
    [4 bytes: message length (big-endian)] [JSON payload]

Message Types:
    - user_message: Input from user
    - assistant_message: Complete response from Progenitor
    - stream_chunk: Streaming response chunk
    - stream_end: End of streaming response
    - heartbeat: Keep-alive ping
    - heartbeat_ack: Keep-alive pong
    - error: Error response
    - shutdown: Graceful shutdown request
"""

import asyncio
import contextlib
import json
import logging
import struct
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)

# Protocol constants
HEADER_SIZE = 4  # 4 bytes for message length (big-endian uint32)
MAX_MESSAGE_SIZE = 10 * 1024 * 1024  # 10MB max message size
ENCODING = "utf-8"


class MessageType(StrEnum):
    """IPC message types."""

    # Request messages (client -> server)
    USER_MESSAGE = "user_message"
    HEARTBEAT = "heartbeat"
    SHUTDOWN = "shutdown"

    # Response messages (server -> client)
    ASSISTANT_MESSAGE = "assistant_message"
    STREAM_CHUNK = "stream_chunk"
    STREAM_END = "stream_end"
    HEARTBEAT_ACK = "heartbeat_ack"
    ERROR = "error"
    READY = "ready"

    # Background event messages (server -> client, pushed asynchronously)
    AGENT_EVENT = "agent_event"


@dataclass
class IPCMessage:
    """IPC message structure."""

    type: MessageType
    content: str = ""
    request_id: str | None = None
    is_final: bool = False
    stream: bool = False
    metadata: dict[str, Any] | None = None
    error_code: str | None = None
    timestamp: str | None = None
    event_type: str | None = None  # For AGENT_EVENT: 'backlog_update', 'schedule_triggered', etc.

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        d: dict[str, Any] = {
            "type": self.type.value if isinstance(self.type, MessageType) else self.type,
            "content": self.content,
        }
        if self.request_id:
            d["request_id"] = self.request_id
        if self.is_final:
            d["is_final"] = self.is_final
        if self.stream:
            d["stream"] = self.stream
        if self.metadata:
            d["metadata"] = self.metadata
        if self.error_code:
            d["error_code"] = self.error_code
        if self.timestamp:
            d["timestamp"] = self.timestamp
        if self.event_type:
            d["event_type"] = self.event_type
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "IPCMessage":
        """Create from dictionary."""
        msg_type = data.get("type", "")
        with contextlib.suppress(ValueError):
            msg_type = MessageType(msg_type)

        return cls(
            type=msg_type,
            content=data.get("content", ""),
            request_id=data.get("request_id"),
            is_final=data.get("is_final", False),
            stream=data.get("stream", False),
            metadata=data.get("metadata"),
            error_code=data.get("error_code"),
            timestamp=data.get("timestamp"),
            event_type=data.get("event_type"),
        )


def encode_message(message: IPCMessage | dict[str, Any]) -> bytes:
    """Encode a message for transmission.

    Args:
        message: IPCMessage or dict to encode.

    Returns:
        Length-prefixed JSON bytes.

    Raises:
        ValueError: If message exceeds maximum size.
    """
    payload = message.to_dict() if isinstance(message, IPCMessage) else message

    json_bytes = json.dumps(payload).encode(ENCODING)

    if len(json_bytes) > MAX_MESSAGE_SIZE:
        raise ValueError(f"Message size {len(json_bytes)} exceeds maximum {MAX_MESSAGE_SIZE}")

    # Length prefix (4 bytes, big-endian)
    header = struct.pack(">I", len(json_bytes))

    return header + json_bytes


def decode_message(data: bytes) -> IPCMessage:
    """Decode a message from bytes (without header).

    Args:
        data: JSON bytes (header already stripped).

    Returns:
        Decoded IPCMessage.
    """
    payload = json.loads(data.decode(ENCODING))
    return IPCMessage.from_dict(payload)


async def read_message(
    reader: asyncio.StreamReader,
    timeout: float | None = None,
) -> IPCMessage | None:
    """Read a message from an async stream.

    Args:
        reader: AsyncIO stream reader.
        timeout: Read timeout in seconds (None = no timeout).

    Returns:
        Decoded IPCMessage or None if connection closed.

    Raises:
        asyncio.TimeoutError: If read times out.
        ValueError: If message is invalid.
    """
    try:
        # Read header
        if timeout:
            header = await asyncio.wait_for(reader.readexactly(HEADER_SIZE), timeout=timeout)
        else:
            header = await reader.readexactly(HEADER_SIZE)

        # Parse length
        (length,) = struct.unpack(">I", header)

        if length > MAX_MESSAGE_SIZE:
            raise ValueError(f"Message size {length} exceeds maximum {MAX_MESSAGE_SIZE}")

        # Read payload
        if timeout:
            payload = await asyncio.wait_for(reader.readexactly(length), timeout=timeout)
        else:
            payload = await reader.readexactly(length)

        return decode_message(payload)

    except asyncio.IncompleteReadError:
        # Connection closed cleanly
        return None
    except TimeoutError:
        raise
    except OSError as e:
        # Handle Windows-specific network errors (e.g., WinError 64)
        logger.warning("Network error reading message: %s", e)
        return None
    except Exception as e:
        logger.error("Error reading message: %s", e)
        raise


async def write_message(
    writer: asyncio.StreamWriter,
    message: IPCMessage | dict[str, Any],
) -> None:
    """Write a message to an async stream.

    Args:
        writer: AsyncIO stream writer.
        message: Message to write.
    """
    data = encode_message(message)
    writer.write(data)
    await writer.drain()


# Convenience functions for creating messages
def user_message(
    content: str,
    request_id: str | None = None,
    stream: bool = False,
    metadata: dict[str, Any] | None = None,
) -> IPCMessage:
    """Create a user message."""
    return IPCMessage(
        type=MessageType.USER_MESSAGE,
        content=content,
        request_id=request_id,
        stream=stream,
        metadata=metadata,
    )


def assistant_message(
    content: str,
    request_id: str | None = None,
) -> IPCMessage:
    """Create an assistant response message."""
    return IPCMessage(
        type=MessageType.ASSISTANT_MESSAGE,
        content=content,
        request_id=request_id,
        is_final=True,
    )


def stream_chunk(
    content: str,
    request_id: str | None = None,
    is_final: bool = False,
) -> IPCMessage:
    """Create a streaming chunk message."""
    return IPCMessage(
        type=MessageType.STREAM_CHUNK,
        content=content,
        request_id=request_id,
        is_final=is_final,
    )


def stream_end(request_id: str | None = None) -> IPCMessage:
    """Create a stream end message."""
    return IPCMessage(
        type=MessageType.STREAM_END,
        request_id=request_id,
        is_final=True,
    )


def error_message(
    content: str,
    error_code: str | None = None,
    request_id: str | None = None,
) -> IPCMessage:
    """Create an error message."""
    return IPCMessage(
        type=MessageType.ERROR,
        content=content,
        error_code=error_code,
        request_id=request_id,
    )


def heartbeat() -> IPCMessage:
    """Create a heartbeat message."""
    return IPCMessage(type=MessageType.HEARTBEAT)


def heartbeat_ack() -> IPCMessage:
    """Create a heartbeat acknowledgment."""
    return IPCMessage(type=MessageType.HEARTBEAT_ACK)


def ready_message() -> IPCMessage:
    """Create a ready message."""
    return IPCMessage(type=MessageType.READY)


def shutdown_message() -> IPCMessage:
    """Create a shutdown request message."""
    return IPCMessage(type=MessageType.SHUTDOWN)


def agent_event(
    event_type: str,
    content: str = "",
    metadata: dict[str, Any] | None = None,
) -> IPCMessage:
    """Create an agent event message for background notifications.

    Args:
        event_type: Type of event (e.g., 'backlog_update', 'schedule_triggered',
                   'task_completed', 'work_started').
        content: Human-readable description of the event.
        metadata: Additional event data (e.g., task details, progress info).

    Returns:
        IPCMessage with type AGENT_EVENT.
    """
    from ..runtime.time_utils import utcnow_isoformat

    return IPCMessage(
        type=MessageType.AGENT_EVENT,
        event_type=event_type,
        content=content,
        metadata=metadata,
        timestamp=utcnow_isoformat(),
    )
