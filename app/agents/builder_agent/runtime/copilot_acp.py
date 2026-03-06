"""
Copilot ACP (Agent Client Protocol) Integration for Progenitor.

Uses the agent-client-protocol SDK to spawn and communicate with
Copilot CLI in ACP mode via stdio, or connect to a remote ACP proxy
over TCP when COPILOT_ACP_URL is configured.

Components:
- CopilotACPManager: Manages the copilot ACP subprocess connection
- ProgenitorACPClient: Client implementation that handles ACP callbacks
- ACPLogCapture: Captures streaming output to log files
"""

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from acp import (
    PROTOCOL_VERSION,
    Client,
    connect_to_agent,
    spawn_agent_process,
    text_block,
)
from acp.schema import (
    AgentMessageChunk,
    AgentPlanUpdate,
    AgentThoughtChunk,
    AllowedOutcome,
    AvailableCommandsUpdate,
    CreateTerminalResponse,
    CurrentModeUpdate,
    DeniedOutcome,
    EnvVariable,
    KillTerminalCommandResponse,
    PermissionOption,
    PromptResponse,
    ReadTextFileResponse,
    ReleaseTerminalResponse,
    RequestPermissionResponse,
    SessionInfoUpdate,
    TerminalOutputResponse,
    ToolCallProgress,
    ToolCallStart,
    ToolCallUpdate,
    UserMessageChunk,
    WaitForTerminalExitResponse,
    WriteTextFileResponse,
)

if TYPE_CHECKING:
    from acp.terminal import TerminalHandle
else:
    TerminalHandle = Any  # type: ignore[assignment]

logger = logging.getLogger(__name__)


class ProgenitorACPClient(Client):
    """Client implementation that handles ACP callbacks from Copilot.

    Collects streaming updates (agent messages, tool calls, thoughts)
    and stores them for later retrieval.
    """

    def __init__(self, work_item_id: str = "unknown"):
        """Initialize the ACP client.

        Args:
            work_item_id: ID of the current work item for logging.
        """
        self.work_item_id = work_item_id
        self.chunks: list[dict[str, Any]] = []
        self.text_parts: list[str] = []
        self.tool_calls: list[dict[str, Any]] = []
        self.errors: list[str] = []
        self._conn: Any = None

    def on_connect(self, conn: Any) -> None:
        """Called when connection is established."""
        self._conn = conn
        logger.info(
            "COPILOT_ACP [%s]: Connected to copilot ACP server",
            self.work_item_id,
        )

    async def session_update(
        self,
        session_id: str,
        update: (
            UserMessageChunk
            | AgentMessageChunk
            | AgentThoughtChunk
            | ToolCallStart
            | ToolCallProgress
            | AgentPlanUpdate
            | AvailableCommandsUpdate
            | CurrentModeUpdate
            | SessionInfoUpdate
        ),
        **_kwargs: Any,
    ) -> None:
        """Handle session updates from the agent.

        This is called for each streaming chunk from Copilot.
        """
        update_type = type(update).__name__

        # Store as chunk for later processing
        chunk_data: dict[str, Any] = {
            "type": update_type,
            "session_id": session_id,
            "timestamp": datetime.now(UTC).isoformat(),
        }

        # Extract content based on update type
        if isinstance(update, AgentMessageChunk):
            # Agent is outputting text - content is a TextContentBlock, ImageContentBlock, etc.
            content = getattr(update, "content", None)
            if content:
                # Content is a TextContentBlock with a `text` field
                text = getattr(content, "text", None)
                if text:
                    self.text_parts.append(text)
                    chunk_data["text"] = text
                    logger.debug(
                        "COPILOT_ACP [%s]: Agent message: %s",
                        self.work_item_id,
                        text[:100] + "..." if len(text) > 100 else text,
                    )

        elif isinstance(update, AgentThoughtChunk):
            # Agent is thinking - content is also a TextContentBlock
            content = getattr(update, "content", None)
            if content:
                text = getattr(content, "text", None)
                if text:
                    chunk_data["thought"] = text
                    logger.debug(
                        "COPILOT_ACP [%s]: Agent thought: %s",
                        self.work_item_id,
                        text[:100] + "..." if len(text) > 100 else text,
                    )

        elif isinstance(update, ToolCallStart):
            # Agent is starting a tool call
            # ToolCallStart has `title` and `tool_call_id` fields
            tool_name = getattr(update, "title", "unknown")
            tool_id = getattr(update, "tool_call_id", "unknown")
            tool_kind = getattr(update, "kind", None)
            chunk_data["tool_call"] = {
                "name": tool_name,
                "id": tool_id,
                "kind": tool_kind,
                "status": "start",
            }
            self.tool_calls.append({"name": tool_name, "id": tool_id, "kind": tool_kind})
            logger.info(
                "COPILOT_ACP [%s]: Tool call started: %s (id=%s, kind=%s)",
                self.work_item_id,
                tool_name,
                tool_id,
                tool_kind,
            )

        elif isinstance(update, ToolCallProgress):
            # Tool call progress update
            tool_id = getattr(update, "tool_call_id", "unknown")
            tool_name = getattr(update, "title", None)
            tool_status = getattr(update, "status", None)
            content = getattr(update, "content", None)
            chunk_data["tool_call"] = {
                "id": tool_id,
                "name": tool_name,
                "status": str(tool_status) if tool_status else "progress",
                "content": str(content) if content else None,
            }
            logger.debug(
                "COPILOT_ACP [%s]: Tool call progress: %s (id=%s, status=%s)",
                self.work_item_id,
                tool_name,
                tool_id,
                tool_status,
            )

        elif isinstance(update, AgentPlanUpdate):
            # Agent plan update
            chunk_data["plan_update"] = True
            logger.debug(
                "COPILOT_ACP [%s]: Plan updated",
                self.work_item_id,
            )

        elif isinstance(update, SessionInfoUpdate):
            # Session info (e.g., mode, model changes)
            logger.debug(
                "COPILOT_ACP [%s]: Session info updated",
                self.work_item_id,
            )

        self.chunks.append(chunk_data)

    async def request_permission(
        self,
        options: list[PermissionOption],
        session_id: str,
        tool_call: ToolCallUpdate,
        **kwargs: Any,
    ) -> RequestPermissionResponse:
        """Handle permission requests from the agent.

        Since we run with --yolo/--allow-all-tools, this shouldn't be called.
        If it is, we auto-approve everything.
        """
        _ = session_id
        _ = tool_call
        _ = kwargs

        logger.warning(
            "COPILOT_ACP [%s]: Permission requested (auto-approving): %s",
            self.work_item_id,
            [getattr(opt, "label", str(opt)) for opt in options],
        )
        # Auto-approve - return the first option with selected outcome
        if options and hasattr(options[0], "id"):
            option_id = getattr(options[0], "id", "default")
            return RequestPermissionResponse(
                outcome=AllowedOutcome(option_id=option_id, outcome="selected")
            )
        # If no options, deny by returning cancelled
        return RequestPermissionResponse(
            outcome=DeniedOutcome(outcome="cancelled")
        )

    # Required interface methods - provide minimal async implementations
    async def create_terminal(
        self,
        command: str,
        session_id: str,
        args: list[str] | None = None,
        cwd: str | None = None,
        env: list[EnvVariable] | None = None,
        output_byte_limit: int | None = None,
        **kwargs: Any,
    ) -> CreateTerminalResponse | TerminalHandle:
        """Handle terminal creation requests."""
        _ = session_id
        _ = args
        _ = cwd
        _ = env
        _ = output_byte_limit
        _ = kwargs
        logger.debug(
            "COPILOT_ACP [%s]: Terminal create requested: %s",
            self.work_item_id,
            command,
        )
        # Return a minimal response - copilot handles terminal internally
        return CreateTerminalResponse(terminal_id="internal")

    async def read_text_file(
        self,
        path: str,
        session_id: str,
        limit: int | None = None,
        line: int | None = None,
        **kwargs: Any,
    ) -> ReadTextFileResponse:
        """Handle file read requests."""
        _ = session_id
        _ = limit
        _ = line
        _ = kwargs
        logger.debug(
            "COPILOT_ACP [%s]: File read requested: %s",
            self.work_item_id,
            path,
        )
        # Copilot handles file reading internally
        return ReadTextFileResponse(content="")

    async def write_text_file(
        self,
        content: str,
        path: str,
        session_id: str,
        **kwargs: Any,
    ) -> WriteTextFileResponse | None:
        """Handle file write requests."""
        _ = content
        _ = session_id
        _ = kwargs
        logger.debug(
            "COPILOT_ACP [%s]: File write requested: %s",
            self.work_item_id,
            path,
        )
        return None

    async def terminal_output(
        self,
        session_id: str,
        terminal_id: str,
        **kwargs: Any,
    ) -> TerminalOutputResponse:
        """Handle terminal output requests."""
        _ = session_id
        _ = terminal_id
        _ = kwargs
        return TerminalOutputResponse(output="", truncated=False)

    async def release_terminal(
        self,
        session_id: str,
        terminal_id: str,
        **kwargs: Any,
    ) -> ReleaseTerminalResponse | None:
        """Handle terminal release requests."""
        _ = session_id
        _ = terminal_id
        _ = kwargs
        return None

    async def kill_terminal(
        self,
        session_id: str,
        terminal_id: str,
        **kwargs: Any,
    ) -> KillTerminalCommandResponse | None:
        """Handle terminal kill requests."""
        _ = session_id
        _ = terminal_id
        _ = kwargs
        return None

    async def wait_for_terminal_exit(
        self,
        session_id: str,
        terminal_id: str,
        **kwargs: Any,
    ) -> WaitForTerminalExitResponse:
        """Handle terminal exit wait requests."""
        _ = session_id
        _ = terminal_id
        _ = kwargs
        return WaitForTerminalExitResponse(exit_code=0)

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Handle extension method calls."""
        _ = params
        logger.debug(
            "COPILOT_ACP [%s]: Extension method: %s",
            self.work_item_id,
            method,
        )
        return {}

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        """Handle extension notifications."""
        _ = params
        logger.debug(
            "COPILOT_ACP [%s]: Extension notification: %s",
            self.work_item_id,
            method,
        )

    def get_output(self) -> str:
        """Get all collected text output."""
        return "".join(self.text_parts)

    def get_chunks(self) -> list[dict[str, Any]]:
        """Get all collected chunks."""
        return self.chunks.copy()

    def has_error(self) -> bool:
        """Check if any errors occurred."""
        return len(self.errors) > 0


class CopilotACPManager:
    """Manages the Copilot ACP connection lifecycle.

    Spawns copilot with --acp flag and communicates via the SDK.
    """

    def __init__(
        self,
        copilot_cmd: str = "copilot",
        model: str | None = None,
        yolo: bool = True,
        allowed_dirs: list[str] | None = None,
        timeout: int = 1200,
        acp_url: str | None = None,
    ):
        """Initialize the ACP manager.

        Args:
            copilot_cmd: Path to the copilot command.
            model: Optional model to use.
            yolo: Whether to auto-approve all tool calls.
            allowed_dirs: Directories copilot is allowed to access.
            timeout: Timeout in seconds for prompts.
            acp_url: Optional TCP URL for remote ACP proxy (e.g. tcp://copilot-acp:3000).
        """
        self.copilot_cmd = copilot_cmd
        self.model = model
        self.yolo = yolo
        self.allowed_dirs = allowed_dirs or []
        self.timeout = timeout
        self.acp_url = acp_url

        logger.info(
            "COPILOT_ACP: Initialized manager with cmd=%s, model=%s, yolo=%s, dirs=%s, acp_url=%s",
            copilot_cmd,
            model,
            yolo,
            allowed_dirs,
            acp_url,
        )

    @staticmethod
    def _normalize_allowed_dirs(allowed_dirs: list[str], cwd: Path) -> list[str]:
        """Normalize allowed dirs to absolute paths under cwd when possible."""
        normalized: list[str] = []
        cwd_resolved = cwd.resolve()
        for raw in allowed_dirs:
            if not raw:
                continue
            p = Path(raw)
            p = (cwd_resolved / p).resolve() if not p.is_absolute() else p.resolve()
            # Defensive: keep Copilot scoped to the worktree/session directory.
            try:
                p.relative_to(cwd_resolved)
            except Exception:
                continue
            normalized.append(str(p))
        return normalized

    def _build_args(self, *, allowed_dirs: list[str], cwd: Path) -> list[str]:
        """Build command-line arguments for copilot."""
        args = ["--acp"]

        if self.model:
            args.extend(["--model", self.model])

        if self.yolo:
            args.append("--yolo")

        for dir_path in self._normalize_allowed_dirs(allowed_dirs, cwd):
            args.extend(["--add-dir", dir_path])

        return args

    @asynccontextmanager
    async def connect(
        self,
        work_item_id: str,
        cwd: Path,
        allowed_dirs: list[str] | None = None,
    ) -> AsyncIterator[tuple["ProgenitorACPClient", Any]]:
        """Connect to copilot ACP and yield the connection.

        Args:
            work_item_id: ID for logging.
            cwd: Working directory for the session.

        Yields:
            Tuple of (ProgenitorACPClient, ClientSideConnection).
        """
        client = ProgenitorACPClient(work_item_id=work_item_id)
        dirs = allowed_dirs if allowed_dirs is not None else self.allowed_dirs
        args = self._build_args(allowed_dirs=dirs, cwd=cwd)

        logger.info(
            "COPILOT_ACP [%s]: Spawning copilot with args: %s %s",
            work_item_id,
            self.copilot_cmd,
            " ".join(args),
        )
        logger.info(
            "COPILOT_ACP [%s]: Working directory: %s",
            work_item_id,
            cwd,
        )

        try:
            async with spawn_agent_process(
                client,
                self.copilot_cmd,
                *args,
                cwd=str(cwd),
            ) as (conn, proc):
                logger.info(
                    "COPILOT_ACP [%s]: Process spawned with PID %s",
                    work_item_id,
                    proc.pid,
                )

                # Initialize the connection
                logger.info(
                    "COPILOT_ACP [%s]: Initializing connection (protocol_version=%d)",
                    work_item_id,
                    PROTOCOL_VERSION,
                )
                init_response = await conn.initialize(protocol_version=PROTOCOL_VERSION)
                logger.info(
                    "COPILOT_ACP [%s]: Initialized: %s",
                    work_item_id,
                    init_response,
                )

                yield client, conn

        except FileNotFoundError as e:
            logger.error(
                "COPILOT_ACP [%s]: Copilot command not found: %s (cmd=%s)",
                work_item_id,
                e,
                self.copilot_cmd,
            )
            raise RuntimeError(
                f"Copilot CLI not found at {self.copilot_cmd}. "
                "Ensure copilot is installed and the path is correct."
            ) from e
        except Exception as e:
            logger.exception(
                "COPILOT_ACP [%s]: Failed to connect: %s",
                work_item_id,
                e,
            )
            raise

    @asynccontextmanager
    async def connect_tcp(
        self,
        work_item_id: str,
        cwd: Path,
    ) -> AsyncIterator[tuple["ProgenitorACPClient", Any]]:
        """Connect to a remote Copilot ACP proxy over TCP.

        Args:
            work_item_id: ID for logging.
            cwd: Working directory for the session.

        Yields:
            Tuple of (ProgenitorACPClient, ClientSideConnection).
        """
        assert self.acp_url, "acp_url must be set for TCP connections"
        parsed = urlparse(self.acp_url)
        host = parsed.hostname or "localhost"
        port = parsed.port or 3000

        client = ProgenitorACPClient(work_item_id=work_item_id)

        logger.info(
            "COPILOT_ACP [%s]: Connecting to remote ACP at %s:%d",
            work_item_id,
            host,
            port,
        )

        tcp_reader, tcp_writer = await asyncio.open_connection(host, port)
        logger.info(
            "COPILOT_ACP [%s]: TCP connection established to %s:%d",
            work_item_id,
            host,
            port,
        )

        conn = connect_to_agent(client, tcp_writer, tcp_reader)
        try:
            init_response = await conn.initialize(protocol_version=PROTOCOL_VERSION)
            logger.info(
                "COPILOT_ACP [%s]: Initialized via TCP: %s",
                work_item_id,
                init_response,
            )
            yield client, conn
        finally:
            await conn.close()
            tcp_writer.close()
            try:
                await tcp_writer.wait_closed()
            except Exception:
                pass
            logger.info(
                "COPILOT_ACP [%s]: TCP connection closed",
                work_item_id,
            )

    async def prompt(
        self,
        prompt_text: str,
        cwd: Path,
        work_item_id: str = "unknown",
        allowed_dirs: list[str] | None = None,
    ) -> dict[str, Any]:
        """Send a prompt to copilot and get the response.

        Args:
            prompt_text: The prompt to send.
            cwd: Working directory.
            work_item_id: ID for logging.

        Returns:
            Dict with success, stdout, stderr, chunks, etc.
        """
        logger.info(
            "COPILOT_ACP [%s]: Starting prompt execution (%d chars)",
            work_item_id,
            len(prompt_text),
        )
        logger.debug(
            "COPILOT_ACP [%s]: Prompt preview: %s",
            work_item_id,
            prompt_text[:500] + "..." if len(prompt_text) > 500 else prompt_text,
        )

        start_time = time.perf_counter()

        try:
            # Use TCP transport if acp_url is configured, otherwise spawn locally
            if self.acp_url:
                connect_cm = self.connect_tcp(work_item_id, cwd)
            else:
                connect_cm = self.connect(work_item_id, cwd, allowed_dirs=allowed_dirs)
            async with connect_cm as (client, conn):
                # Create a new session
                logger.info(
                    "COPILOT_ACP [%s]: Creating new session (cwd=%s)",
                    work_item_id,
                    cwd,
                )
                session_response = await conn.new_session(
                    cwd=str(cwd),
                    mcp_servers=[],
                )
                session_id = session_response.session_id
                logger.info(
                    "COPILOT_ACP [%s]: Session created: %s",
                    work_item_id,
                    session_id,
                )

                # Send the prompt
                logger.info(
                    "COPILOT_ACP [%s]: Sending prompt to session %s",
                    work_item_id,
                    session_id,
                )

                prompt_response: PromptResponse = await asyncio.wait_for(
                    conn.prompt(
                        prompt=[text_block(prompt_text)],
                        session_id=session_id,
                    ),
                    timeout=self.timeout,
                )

                elapsed = time.perf_counter() - start_time
                stop_reason = getattr(prompt_response, "stop_reason", None)

                logger.info(
                    "COPILOT_ACP [%s]: Prompt completed in %.2fs, stop_reason=%s",
                    work_item_id,
                    elapsed,
                    stop_reason,
                )
                logger.info(
                    "COPILOT_ACP [%s]: Collected %d chunks, %d tool calls",
                    work_item_id,
                    len(client.chunks),
                    len(client.tool_calls),
                )

                # Determine success
                success = stop_reason in ("end_turn", "stop", None) and not client.has_error()

                output = client.get_output()
                logger.info(
                    "COPILOT_ACP [%s]: Output length: %d chars",
                    work_item_id,
                    len(output),
                )

                return {
                    "success": success,
                    "stdout": output,
                    "stderr": "\n".join(client.errors) if client.errors else "",
                    "stop_reason": str(stop_reason) if stop_reason else None,
                    "chunks": client.get_chunks(),
                    "tool_calls": client.tool_calls,
                    "elapsed_seconds": elapsed,
                }

        except TimeoutError:
            elapsed = time.perf_counter() - start_time
            logger.error(
                "COPILOT_ACP [%s]: Prompt timed out after %.2fs (timeout=%ds)",
                work_item_id,
                elapsed,
                self.timeout,
            )
            return {
                "success": False,
                "stdout": "",
                "stderr": f"Timeout after {self.timeout}s",
                "stop_reason": "timeout",
                "chunks": [],
                "tool_calls": [],
                "elapsed_seconds": elapsed,
            }
        except Exception as e:
            elapsed = time.perf_counter() - start_time
            logger.exception(
                "COPILOT_ACP [%s]: Prompt failed after %.2fs: %s",
                work_item_id,
                elapsed,
                e,
            )
            return {
                "success": False,
                "stdout": "",
                "stderr": str(e),
                "stop_reason": "error",
                "chunks": [],
                "tool_calls": [],
                "elapsed_seconds": elapsed,
                "error": str(e),
            }

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

    def _generate_log_base_path(self, work_item_id: str) -> Path:
        """Generate a unique log path prefix for a session.

        Args:
            work_item_id: Work item ID for the build.

        Returns:
            Path prefix without extension.
        """
        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        filename = f"{timestamp}_{work_item_id}"
        return self.logs_dir / filename

    def write_log(
        self,
        work_item_id: str,
        result: dict[str, Any],
    ) -> Path:
        """Write the result to a log file.

        Args:
            work_item_id: Work item ID.
            result: Result dict from prompt execution.

        Returns:
            Path to the created log file.
        """
        base_path = self._generate_log_base_path(work_item_id)
        log_path = base_path.with_suffix(".log")
        stdout_path = base_path.with_suffix(".stdout.log")
        stderr_path = base_path.with_suffix(".stderr.log")

        logger.info(
            "COPILOT_ACP_LOG: Writing session log to %s",
            log_path,
        )

        try:
            with open(log_path, "w", encoding="utf-8") as f:
                f.write("# Copilot ACP Session Log\n")
                f.write(f"# Work Item: {work_item_id}\n")
                f.write(f"# Timestamp: {datetime.now(UTC).isoformat()}\n")
                f.write(f"# Success: {result.get('success')}\n")
                f.write(f"# Stop Reason: {result.get('stop_reason')}\n")
                f.write(f"# Elapsed: {result.get('elapsed_seconds', 0):.2f}s\n")
                f.write("# " + "=" * 70 + "\n\n")

                # Write output
                f.write("## Output\n\n")
                f.write(result.get("stdout", ""))
                f.write("\n\n")

                # Write errors if any
                if result.get("stderr"):
                    f.write("## Errors\n\n")
                    f.write(result.get("stderr", ""))
                    f.write("\n\n")

                # Write tool calls
                if result.get("tool_calls"):
                    f.write("## Tool Calls\n\n")
                    for tc in result.get("tool_calls", []):
                        f.write(f"- {tc.get('name', 'unknown')} (id={tc.get('id', 'unknown')})\n")
                    f.write("\n")

                # Write chunk summary
                f.write("## Summary\n\n")
                f.write(f"- Total chunks: {len(result.get('chunks', []))}\n")
                f.write(f"- Tool calls: {len(result.get('tool_calls', []))}\n")
                f.write(f"- Output length: {len(result.get('stdout', ''))} chars\n")
                f.write(f"- stdout log: {stdout_path.name}\n")
                f.write(f"- stderr log: {stderr_path.name}\n")

            with open(stdout_path, "w", encoding="utf-8") as f:
                f.write(result.get("stdout", ""))

            with open(stderr_path, "w", encoding="utf-8") as f:
                f.write(result.get("stderr", ""))

        except Exception as e:
            logger.exception("COPILOT_ACP_LOG: Failed to write log: %s", e)

        return log_path
