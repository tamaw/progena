"""
Mock Progenitor IPC Server for Integration Testing

This provides a minimal IPC server that responds to messages
for testing purposes when the full Progenitor agent can't be started.
"""

import asyncio
import logging
import sys
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.progenitor.ipc.protocol import (
    MessageType,
    assistant_message,
    heartbeat_ack,
    read_message,
    ready_message,
    stream_chunk,
    write_message,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9500


class MockProgenitorServer:
    """Mock IPC server that provides simple responses for testing."""

    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT):
        self.host = host
        self.port = port
        self._server = None
        self._running = False

    async def start(self):
        """Start the mock server."""
        self._server = await asyncio.start_server(
            self._handle_client,
            self.host,
            self.port,
            reuse_address=True,
        )
        self._running = True
        logger.info(f"Mock Progenitor IPC server listening on {self.host}:{self.port}")

    async def serve_forever(self):
        """Run the server until stopped."""
        if self._server:
            async with self._server:
                await asyncio.Event().wait()

    async def _handle_client(self, reader, writer):
        """Handle a client connection."""
        peer = writer.get_extra_info("peername")
        logger.info(f"Client connected from {peer}")

        try:
            # Send ready message
            await write_message(writer, ready_message())

            while self._running:
                try:
                    msg = await read_message(reader, timeout=300)
                except TimeoutError:
                    logger.debug("Client timed out")
                    break

                if msg is None:
                    logger.info("Client disconnected")
                    break

                # Handle message
                if msg.type == MessageType.HEARTBEAT:
                    await write_message(writer, heartbeat_ack())

                elif msg.type == MessageType.USER_MESSAGE:
                    logger.info(f"Received message: {msg.content[:100]}...")

                    # Generate mock response based on content
                    response = self._generate_response(msg.content)

                    if msg.stream:
                        # Send streaming response
                        chunks = self._split_into_chunks(response)
                        for i, chunk in enumerate(chunks):
                            is_final = (i == len(chunks) - 1)
                            await write_message(
                                writer,
                                stream_chunk(chunk, msg.request_id, is_final=is_final)
                            )
                            await asyncio.sleep(0.1)  # Simulate streaming delay
                    else:
                        # Send complete response
                        await write_message(
                            writer,
                            assistant_message(response, msg.request_id)
                        )

                    logger.info(f"Sent response ({len(response)} chars)")

        except Exception as e:
            logger.error(f"Error handling client: {e}", exc_info=True)
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            logger.info("Client connection closed")

    def _generate_response(self, content: str) -> str:
        """Generate a mock response based on the input."""
        content_lower = content.lower()

        # Simple pattern matching for test cases
        if "2 + 2" in content_lower or "2+2" in content_lower:
            return "2 + 2 equals 4."

        if "three programming languages" in content_lower or "list three" in content_lower:
            return "Here are three programming languages:\n1. Python\n2. JavaScript\n3. Rust"

        if "schedule" in content_lower:
            return "I've created a scheduled task to run in 2 hours: 'Check system status and report any issues'. The task has been added to the schedule."

        if "agent" in content_lower and "build" in content_lower:
            return "To build an agent that checks if a file exists, I would:\n1. Create a new agent module\n2. Implement a file existence checker function\n3. Add proper error handling\n4. Register the agent in the system\nThis would be a simple build task."

        if "agents are currently available" in content_lower or "list" in content_lower and "agent" in content_lower:
            return "Currently available agents:\n- file_checker: Checks file existence\n- code_analyzer: Analyzes code quality\n- test_runner: Runs automated tests"

        if "current directory" in content_lower or "what files" in content_lower:
            return "Current directory: /workspace/progenitor\nFiles: README.md, app/, tests/, data/, logs/"

        if "name" in content_lower and "testuser" in content_lower:
            return "Got it! I'll remember that your name is TestUser."

        if "my name" in content_lower:
            return "Your name is TestUser."

        # Default response
        return f"Mock response to: {content[:50]}... (This is a test response from the mock Progenitor server)"

    def _split_into_chunks(self, text: str, chunk_size: int = 20) -> list[str]:
        """Split text into chunks for streaming."""
        return [text[i:i+chunk_size] for i in range(0, len(text), chunk_size)]


async def main():
    """Run the mock server."""
    server = MockProgenitorServer()
    await server.start()
    
    logger.info("Mock Progenitor IPC server ready for testing")
    logger.info("Press Ctrl+C to stop")
    
    try:
        await server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down...")


if __name__ == "__main__":
    asyncio.run(main())
