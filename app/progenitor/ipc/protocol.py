"""
IPC Message Protocol for Progenitor.

Defines the wire format for communication between chat_server and Progenitor.
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
import json
import logging
import struct
from dataclasses import dataclass, asdict
from enum import Enum
from typing import Any, Optional, Union

logger = logging.getLogger(__name__)

# Protocol constants
HEADER_SIZE = 4  # 4 bytes for message length (big-endian uint32)
MAX_MESSAGE_SIZE = 10 * 1024 * 1024  # 10MB max message size
ENCODING = "utf-8"


class MessageType(str, Enum):
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


@dataclass
class IPCMessage:
    """IPC message structure."""

    type: MessageType
    content: str = ""
    request_id: Optional[str] = None
    is_final: bool = False
    stream: bool = False
    metadata: Optional[dict[str, Any]] = None
    error_code: Optional[str] = None
    timestamp: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        d = {
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
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "IPCMessage":
        """Create from dictionary."""
        msg_type = data.get("type", "")
        try:
            msg_type = MessageType(msg_type)
        except ValueError:
            # Keep as string if unknown type
            pass

        return cls(
            type=msg_type,
            content=data.get("content", ""),
            request_id=data.get("request_id"),
            is_final=data.get("is_final", False),
            stream=data.get("stream", False),
            metadata=data.get("metadata"),
            error_code=data.get("error_code"),
            timestamp=data.get("timestamp"),
        )


def encode_message(message: Union[IPCMessage, dict[str, Any]]) -> bytes:
    """Encode a message for transmission.

    Args:
        message: IPCMessage or dict to encode.

    Returns:
        Length-prefixed JSON bytes.

    Raises:
        ValueError: If message exceeds maximum size.
    """
    if isinstance(message, IPCMessage):
        payload = message.to_dict()
    else:
        payload = message

    json_bytes = json.dumps(payload).encode(ENCODING)

    if len(json_bytes) > MAX_MESSAGE_SIZE:
        raise ValueError(
            f"Message size {len(json_bytes)} exceeds maximum {MAX_MESSAGE_SIZE}"
        )

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
    timeout: Optional[float] = None,
) -> Optional[IPCMessage]:
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
            header = await asyncio.wait_for(
                reader.readexactly(HEADER_SIZE), timeout=timeout
            )
        else:
            header = await reader.readexactly(HEADER_SIZE)

        # Parse length
        (length,) = struct.unpack(">I", header)

        if length > MAX_MESSAGE_SIZE:
            raise ValueError(f"Message size {length} exceeds maximum {MAX_MESSAGE_SIZE}")

        # Read payload
        if timeout:
            payload = await asyncio.wait_for(
                reader.readexactly(length), timeout=timeout
            )
        else:
            payload = await reader.readexactly(length)

        return decode_message(payload)

    except asyncio.IncompleteReadError:
        # Connection closed cleanly
        return None
    except asyncio.TimeoutError:
        raise
    except Exception as e:
        logger.error("Error reading message: %s", e)
        raise


async def write_message(
    writer: asyncio.StreamWriter,
    message: Union[IPCMessage, dict[str, Any]],
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
    request_id: Optional[str] = None,
    stream: bool = False,
) -> IPCMessage:
    """Create a user message."""
    return IPCMessage(
        type=MessageType.USER_MESSAGE,
        content=content,
        request_id=request_id,
        stream=stream,
    )


def assistant_message(
    content: str,
    request_id: Optional[str] = None,
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
    request_id: Optional[str] = None,
    is_final: bool = False,
) -> IPCMessage:
    """Create a streaming chunk message."""
    return IPCMessage(
        type=MessageType.STREAM_CHUNK,
        content=content,
        request_id=request_id,
        is_final=is_final,
    )


def stream_end(request_id: Optional[str] = None) -> IPCMessage:
    """Create a stream end message."""
    return IPCMessage(
        type=MessageType.STREAM_END,
        request_id=request_id,
        is_final=True,
    )


def error_message(
    content: str,
    error_code: Optional[str] = None,
    request_id: Optional[str] = None,
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
