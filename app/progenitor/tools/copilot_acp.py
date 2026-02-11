"""
Copilot ACP (Agent Client Protocol) Integration for Progenitor.

Manages the Copilot ACP server lifecycle and provides a client for
communicating via the Agent Client Protocol instead of subprocess stdin/stdout.

Components:
- CopilotACPService: Manages copilot --acp --port 3000 background process
- CopilotACPClient: Communicates with the ACP server via SDK
- ACPLogCapture: Captures streaming output to log files
"""

import asyncio
import contextlib
import importlib.util
import logging
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class CopilotACPService:
    """Manages the Copilot ACP server lifecycle.

    Starts copilot with --acp --port flags as a background process,
    monitors its health, and provides graceful shutdown.
    """

    def __init__(
        self,
        port: int = 3000,
        model: str | None = None,
        yolo: bool = True,
        allowed_dirs: list[str] | None = None,
    ):
        """Initialize the ACP service manager.

        Args:
            port: Port for the ACP server to listen on.
            model: Optional model to use for Copilot.
            yolo: Whether to auto-approve file edits (--yolo flag).
            allowed_dirs: Directories Copilot is allowed to access.
        """
        self.port = port
        self.model = model
        self.yolo = yolo
        self.allowed_dirs = allowed_dirs or []

        self.process: asyncio.subprocess.Process | None = None
        self._started = asyncio.Event()
        self._stdout_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self._stdout_lines: list[str] = []
        self._stderr_lines: list[str] = []

    async def start(self) -> None:
        """Start copilot with --acp --port as a background process."""
        if self.process is not None:
            logger.warning("COPILOT_ACP: Service already started")
            return

        cmd = self._build_command()
        logger.info("COPILOT_ACP: Starting service with: %s", " ".join(cmd))

        try:
            self.process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            self._stdout_task = asyncio.create_task(
                self._monitor_stream(self.process.stdout, "stdout", self._stdout_lines)
            )
            self._stderr_task = asyncio.create_task(
                self._monitor_stream(self.process.stderr, "stderr", self._stderr_lines)
            )

        except FileNotFoundError as e:
            logger.error("COPILOT_ACP: copilot command not found: %s", e)
            raise RuntimeError("copilot CLI not found - ensure it is installed and in PATH") from e
        except Exception as e:
            logger.exception("COPILOT_ACP: Failed to start service: %s", e)
            raise

    def _build_command(self) -> list[str]:
        """Build the command line for starting the ACP server."""
        cmd = ["copilot", "--acp", "--port", str(self.port)]

        if self.model:
            cmd.extend(["--model", self.model])

        if self.yolo:
            cmd.append("--yolo")

        for dir_path in self.allowed_dirs:
            cmd.extend(["--add-dir", dir_path])

        return cmd

    async def _monitor_stream(
        self,
        stream: asyncio.StreamReader | None,
        name: str,
        buffer: list[str],
    ) -> None:
        """Monitor a stream and log output."""
        if stream is None:
            return

        while True:
            try:
                line = await stream.readline()
                if not line:
                    break

                decoded = line.decode().rstrip()
                buffer.append(decoded)

                # Check for ready indicator
                if "listening" in decoded.lower() or "ready" in decoded.lower():
                    self._started.set()
                    logger.info("COPILOT_ACP [%s]: Server ready - %s", name, decoded)
                else:
                    logger.debug("COPILOT_ACP [%s]: %s", name, decoded)

            except Exception as e:
                logger.warning("COPILOT_ACP: Error reading %s: %s", name, e)
                break

    async def wait_ready(self, timeout: float = 30.0) -> bool:
        """Wait for the ACP server to be ready.

        Args:
            timeout: Maximum seconds to wait for server to be ready.

        Returns:
            True if server is ready, False if timeout occurred.
        """
        if self.process is None:
            logger.error("COPILOT_ACP: Cannot wait for ready - service not started")
            return False

        try:
            # Wait for the _started event or timeout
            await asyncio.wait_for(self._started.wait(), timeout=timeout)
            return True
        except TimeoutError:
            # Even if we don't see "ready", check if process is still running
            if self.process.returncode is None:
                logger.warning(
                    "COPILOT_ACP: No ready indicator seen within %ds, "
                    "but process is running - assuming ready",
                    timeout
                )
                self._started.set()
                return True
            else:
                logger.error(
                    "COPILOT_ACP: Process exited with code %s before ready",
                    self.process.returncode
                )
                return False

    async def stop(self) -> None:
        """Gracefully stop the Copilot ACP server."""
        if self.process is None:
            logger.debug("COPILOT_ACP: Service not running, nothing to stop")
            return

        logger.info("COPILOT_ACP: Stopping service (PID %s)...", self.process.pid)

        # Cancel monitoring tasks
        for task in [self._stdout_task, self._stderr_task]:
            if task and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        # Graceful shutdown with SIGTERM, then SIGKILL
        try:
            self.process.terminate()
            logger.debug("COPILOT_ACP: Sent SIGTERM to process %s", self.process.pid)

            try:
                await asyncio.wait_for(self.process.wait(), timeout=5.0)
                logger.info("COPILOT_ACP: Process terminated gracefully")
            except TimeoutError:
                logger.warning("COPILOT_ACP: Process did not respond to SIGTERM, sending SIGKILL")
                self.process.kill()
                await self.process.wait()
                logger.info("COPILOT_ACP: Process killed")

        except ProcessLookupError:
            logger.debug("COPILOT_ACP: Process already terminated")
        except Exception as e:
            logger.warning("COPILOT_ACP: Error stopping process: %s", e)

        self.process = None
        self._started.clear()
        self._stdout_task = None
        self._stderr_task = None
        self._stdout_lines.clear()
        self._stderr_lines.clear()

    @property
    def is_running(self) -> bool:
        """Check if the ACP service is currently running."""
        return self.process is not None and self.process.returncode is None


class CopilotACPClient:
    """Client for communicating with Copilot ACP server.

    Uses the agent-client-protocol SDK to establish a session
    and send prompts to the Copilot ACP server.
    """

    def __init__(
        self,
        port: int = 3000,
        timeout: int = 1200,  # 20 minutes per request
    ):
        """Initialize the ACP client.

        Args:
            port: Port where the ACP server is listening.
            timeout: Timeout in seconds for each prompt request.
        """
        self.port = port
        self.timeout = timeout
        self._connection: Any = None  # Will hold the ACP connection
        self._session_id: str | None = None
        self._initialized = False

    async def initialize(self) -> None:
        """Initialize the connection to the ACP server.

        Creates a connection and establishes an initial session.
        """
        if self._initialized:
            logger.debug("COPILOT_ACP_CLIENT: Already initialized")
            return

        try:
            # Import ACP SDK types
            if importlib.util.find_spec("acp") is None:
                raise ImportError("acp")

            logger.info("COPILOT_ACP_CLIENT: Connecting to ACP server on port %d", self.port)

            # Connect to the ACP server via TCP
            # The SDK provides a way to connect to a running ACP server
            reader, writer = await asyncio.open_connection("127.0.0.1", self.port)

            # Create a simple transport wrapper for the ACP protocol
            self._reader = reader
            self._writer = writer
            self._initialized = True

            logger.info("COPILOT_ACP_CLIENT: Connected to ACP server")

        except ImportError as e:
            logger.error("COPILOT_ACP_CLIENT: agent-client-protocol SDK not installed: %s", e)
            raise RuntimeError(
                "agent-client-protocol SDK not installed. "
                "Run: pip install agent-client-protocol"
            ) from e
        except ConnectionRefusedError as e:
            logger.error("COPILOT_ACP_CLIENT: Could not connect to ACP server on port %d", self.port)
            raise RuntimeError(
                f"Could not connect to Copilot ACP server on port {self.port}. "
                "Ensure the CopilotACPService is running."
            ) from e
        except Exception as e:
            logger.exception("COPILOT_ACP_CLIENT: Failed to initialize: %s", e)
            raise

    async def create_session(self, cwd: Path) -> str:
        """Create a new session for a build operation.

        Args:
            cwd: Working directory for the session.

        Returns:
            Session ID for the new session.
        """
        if not self._initialized:
            await self.initialize()

        try:
            import json

            # Send new session request via JSON-RPC
            request_id = f"session_{int(time.time() * 1000)}"
            request = {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "new_session",
                "params": {
                    "cwd": str(cwd),
                    "mcp_servers": [],
                }
            }

            self._writer.write((json.dumps(request) + "\n").encode())
            await self._writer.drain()

            # Read response
            response_line = await asyncio.wait_for(
                self._reader.readline(),
                timeout=30.0
            )
            response = json.loads(response_line.decode())

            if "error" in response:
                raise RuntimeError(f"ACP new_session failed: {response['error']}")

            self._session_id = response.get("result", {}).get("session_id")
            logger.info("COPILOT_ACP_CLIENT: Created session %s", self._session_id)
            return self._session_id or ""

        except TimeoutError:
            logger.error("COPILOT_ACP_CLIENT: Timeout creating session")
            raise RuntimeError("Timeout creating ACP session")
        except Exception as e:
            logger.exception("COPILOT_ACP_CLIENT: Failed to create session: %s", e)
            raise

    async def prompt(
        self,
        prompt_text: str,
        cwd: Path,
        session_id: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Send a prompt and stream the response.

        Args:
            prompt_text: The prompt to send to Copilot.
            cwd: Working directory for the operation.
            session_id: Optional session ID (creates new if not provided).

        Yields:
            Response chunks from the ACP server.
        """
        if not self._initialized:
            await self.initialize()

        # Create or use existing session
        sid = session_id or self._session_id
        if not sid:
            sid = await self.create_session(cwd)

        try:
            import json


            # Send prompt request
            request_id = f"prompt_{int(time.time() * 1000)}"
            request = {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "prompt",
                "params": {
                    "session_id": sid,
                    "prompt": [{"type": "text", "text": prompt_text}],
                }
            }

            logger.info("COPILOT_ACP_CLIENT: Sending prompt (%d chars) to session %s",
                       len(prompt_text), sid)

            self._writer.write((json.dumps(request) + "\n").encode())
            await self._writer.drain()

            # Stream responses with timeout
            start_time = time.time()

            while True:
                elapsed = time.time() - start_time
                remaining_timeout = self.timeout - elapsed

                if remaining_timeout <= 0:
                    logger.warning("COPILOT_ACP_CLIENT: Prompt timed out after %ds", self.timeout)
                    yield {
                        "type": "error",
                        "error": f"Request timed out after {self.timeout}s",
                    }
                    break

                try:
                    line = await asyncio.wait_for(
                        self._reader.readline(),
                        timeout=min(remaining_timeout, 30.0)  # Check timeout every 30s
                    )

                    if not line:
                        logger.warning("COPILOT_ACP_CLIENT: Connection closed")
                        break

                    response = json.loads(line.decode())
                    if "error" in response:
                        yield {
                            "type": "error",
                            "error": response["error"],
                        }
                        break

                    result = response.get("result", {})

                    # Yield the result for processing
                    yield result

                    # Check for completion
                    stop_reason = result.get("stop_reason")
                    if stop_reason:
                        logger.info("COPILOT_ACP_CLIENT: Prompt completed with stop_reason=%s",
                                   stop_reason)
                        break

                except TimeoutError:
                    # Continue loop to check overall timeout
                    continue

        except Exception as e:
            logger.exception("COPILOT_ACP_CLIENT: Error during prompt: %s", e)
            yield {
                "type": "error",
                "error": str(e),
            }

    async def close(self) -> None:
        """Close the ACP connection."""
        if not self._initialized:
            return

        try:
            if self._writer:
                self._writer.close()
                await self._writer.wait_closed()

            logger.info("COPILOT_ACP_CLIENT: Connection closed")

        except Exception as e:
            logger.warning("COPILOT_ACP_CLIENT: Error closing connection: %s", e)

        self._connection = None
        self._session_id = None
        self._initialized = False


class ACPLogCapture:
    """Captures ACP streaming output to log files.

    Each build session creates a new log file containing the
    full streaming output from Copilot for debugging and auditing.
    """

    def __init__(self, logs_dir: Path):
        """Initialize the log capture.

        Args:
            logs_dir: Base directory for logs (will create copilot_acp subdir).
        """
        self.logs_dir = logs_dir / "copilot_acp"
        self.logs_dir.mkdir(parents=True, exist_ok=True)

    def _generate_log_path(self, work_item_id: str) -> Path:
        """Generate a unique log file path for a session.

        Args:
            work_item_id: Work item ID for the build.

        Returns:
            Path to the log file.
        """
        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        filename = f"{timestamp}_{work_item_id}.log"
        return self.logs_dir / filename

    async def capture_stream(
        self,
        work_item_id: str,
        stream: AsyncIterator[dict[str, Any]],
    ) -> tuple[str, list[dict[str, Any]]]:
        """Capture all streaming output to a log file.

        Args:
            work_item_id: Work item ID for the build.
            stream: Async iterator of response chunks from ACP client.

        Returns:
            Tuple of (full output text, list of all chunks).
        """
        log_path = self._generate_log_path(work_item_id)
        chunks: list[dict[str, Any]] = []
        output_parts: list[str] = []

        logger.info("COPILOT_ACP_LOG: Capturing session output to %s", log_path)

        try:
            with open(log_path, "w", encoding="utf-8") as f:
                f.write("# Copilot ACP Session Log\n")
                f.write(f"# Work Item: {work_item_id}\n")
                f.write(f"# Started: {datetime.now(UTC).isoformat()}\n")
                f.write("# " + "=" * 70 + "\n\n")

                async for chunk in stream:
                    chunks.append(chunk)

                    # Extract text content from chunk
                    chunk_type = chunk.get("type", "unknown")

                    if chunk_type == "error":
                        error_msg = chunk.get("error", "Unknown error")
                        f.write(f"[ERROR] {error_msg}\n")
                        output_parts.append(f"[ERROR] {error_msg}")

                    elif "content" in chunk:
                        # Content blocks from the response
                        for block in chunk.get("content", []):
                            if isinstance(block, dict):
                                text = block.get("text", "")
                                if text:
                                    f.write(text)
                                    output_parts.append(text)
                            elif isinstance(block, str):
                                f.write(block)
                                output_parts.append(block)

                    elif "text" in chunk:
                        text = chunk["text"]
                        f.write(text)
                        output_parts.append(text)

                    # Also log tool calls for debugging
                    if "tool_calls" in chunk:
                        for tc in chunk.get("tool_calls", []):
                            tool_name = tc.get("name", "unknown")
                            f.write(f"\n[TOOL CALL] {tool_name}\n")

                    # Flush after each chunk for real-time logging
                    f.flush()

                f.write("\n\n# " + "=" * 70 + "\n")
                f.write(f"# Completed: {datetime.now(UTC).isoformat()}\n")
                f.write(f"# Total chunks: {len(chunks)}\n")

            logger.info("COPILOT_ACP_LOG: Session complete - %d chunks captured", len(chunks))

        except Exception as e:
            logger.exception("COPILOT_ACP_LOG: Error capturing session: %s", e)

        return "".join(output_parts), chunks
