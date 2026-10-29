"""Generic ACP runner: spawn an ACP agent over stdio and run one prompt.

``ACPRunner`` connects to an ACP agent — either by spawning ``command *args`` as
a local stdio subprocess, or by connecting over **TCP** to an already-running
server (e.g. ``copilot --acp --port 3000``) — performs the ACP handshake
(``initialize`` → ``session/new`` → ``session/prompt``), collects the streamed
output via :class:`ACPSessionClient`, and returns a normalised result.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
import os
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from acp import PROTOCOL_VERSION, connect_to_agent, spawn_agent_process, text_block

from .client import ACPSessionClient

logger = logging.getLogger(__name__)

# stop_reason values that indicate the prompt finished cleanly.
_OK_STOP_REASONS = {"end_turn", "stop", "completed", None}


@dataclass
class ACPRunResult:
    """Normalised outcome of a single ACP prompt run."""

    success: bool
    output: str = ""
    output_char_count: int = 0
    output_truncated: bool = False
    stop_reason: str | None = None
    session_id: str | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_call_count: int = 0
    elapsed_seconds: float = 0.0
    error: str | None = None
    cancelled: bool = False


class ACPRunner:
    """Run one prompt against an ACP agent over stdio (spawned) or TCP (connect).

    - **stdio** (default): spawn ``command *args`` as a subprocess and drive it.
    - **TCP**: set ``port`` (and optionally ``host``) to connect to an already
      running ACP server (e.g. ``copilot --acp --port 3000``); ``model`` is then
      applied per session through ``session/set_config_option``.
    """

    def __init__(
        self,
        command: str | None = None,
        args: Sequence[str] | None = None,
        *,
        host: str | None = None,
        port: int | None = None,
        model: str | None = None,
        timeout: int = 1200,
    ) -> None:
        self.command = command
        self.args = list(args or [])
        self.host = host
        self.port = port
        self.model = model
        self.timeout = timeout

        self._active_conn: Any | None = None
        self._active_session_id: str | None = None
        self._cancel_requested = False

    async def cancel(self, session_id: str | None = None) -> bool:
        """Ask the active ACP connection to cancel its current prompt."""
        conn = self._active_conn
        active_session_id = session_id or self._active_session_id
        if conn is None or not active_session_id:
            return False
        try:
            await conn.cancel(session_id=active_session_id)
        except Exception:
            logger.debug("ACP cancel failed for session %s", active_session_id, exc_info=True)
            return False
        self._cancel_requested = True
        return True

    async def steer(self, session_id: str, text: str) -> str:
        """Inject text into the active turn on this runner's live connection."""
        conn = self._active_conn
        if conn is None or session_id != self._active_session_id:
            return "promptRequired"
        response = await conn.ext_method(
            "session/steering",
            {
                "sessionId": session_id,
                "prompt": [text_block(text).model_dump(mode="json", by_alias=True)],
                "_meta": {"steering": {"idleBehavior": "promptRequired"}},
            },
        )
        outcome = response.get("outcome") if isinstance(response, dict) else None
        return str(outcome or "failed")

    async def set_mode(self, mode: str) -> bool:
        """Set the mode of this runner's active ACP session."""
        conn = self._active_conn
        session_id = self._active_session_id
        if conn is None or not session_id:
            return False
        await conn.set_session_mode(session_id=session_id, mode_id=mode)
        return True

    async def _set_session_model(self, conn: Any, session_id: str) -> None:
        """Select the configured model through ACP's typed config-option API."""
        if self.model:
            await conn.set_config_option(
                config_id="model",
                session_id=session_id,
                value=self.model,
            )

    @property
    def is_tcp(self) -> bool:
        return self.port is not None

    @contextlib.asynccontextmanager
    async def _open(self, client: ACPSessionClient, cwd: Path, run_id: str) -> AsyncIterator[Any]:
        """Yield a connected ``ClientSideConnection`` for stdio or TCP."""
        if self.is_tcp:
            host = self.host or "127.0.0.1"
            logger.info("ACP [%s]: connecting to %s:%s", run_id, host, self.port)
            reader, writer = await asyncio.open_connection(host, self.port)
            conn = connect_to_agent(client, writer, reader, use_unstable_protocol=True)
            try:
                yield conn
            finally:
                with contextlib.suppress(Exception):
                    await conn.close()
                writer.close()
                with contextlib.suppress(Exception):
                    await writer.wait_closed()
        else:
            logger.info(
                "ACP [%s]: spawning %s %s (cwd=%s)",
                run_id,
                self.command,
                " ".join(self.args),
                cwd,
            )
            async with spawn_agent_process(
                client,
                self.command or "",
                *self.args,
                cwd=str(cwd),
                env=os.environ.copy(),
                use_unstable_protocol=True,
            ) as (conn, proc):
                yield conn

    async def run(
        self,
        prompt_text: str,
        cwd: Path | str,
        *,
        run_id: str = "unknown",
        session_id: str | None = None,
        mode: str | None = None,
        on_progress: Callable[[dict[str, Any]], Awaitable[Any] | Any] | None = None,
        on_input: Callable[[dict[str, Any]], Awaitable[Any] | Any] | None = None,
        max_output_chars: int | None = None,
    ) -> ACPRunResult:
        cwd_path = Path(cwd)
        start = time.perf_counter()
        output_limited = False

        async def forward_progress(update: dict[str, Any]) -> None:
            nonlocal output_limited
            if on_progress is None:
                return
            ready = on_progress(update)
            if inspect.isawaitable(ready):
                ready = await ready
            if ready is True and not output_limited:
                client.set_output_limit(max_output_chars)
                output_limited = True

        client = ACPSessionClient(
            run_id=run_id,
            on_progress=forward_progress,
            on_input=on_input,
        )
        active_session_id = session_id

        try:
            async with self._open(client, cwd_path, run_id) as conn:
                await conn.initialize(
                    protocol_version=PROTOCOL_VERSION,
                    client_capabilities=client.capabilities,
                )
                if session_id:
                    session = await conn.resume_session(
                        cwd=str(cwd_path),
                        session_id=session_id,
                        mcp_servers=[],
                    )
                else:
                    session = await conn.new_session(cwd=str(cwd_path), mcp_servers=[])
                active_session_id = getattr(session, "session_id", None) or session_id
                if not active_session_id:
                    raise RuntimeError("ACP agent did not return a session id")
                self._active_conn = conn
                self._active_session_id = active_session_id
                self._cancel_requested = False
                await forward_progress({"kind": "session", "session_id": active_session_id})
                if self.is_tcp and self.model:
                    await self._set_session_model(conn, active_session_id)
                if mode:
                    await self.set_mode(mode)
                response = await asyncio.wait_for(
                    conn.prompt(prompt=[text_block(prompt_text)], session_id=active_session_id),
                    timeout=self.timeout,
                )
                stop_reason = getattr(response, "stop_reason", None)
                stop_reason_text = str(stop_reason) if stop_reason is not None else None
                cancelled = self._cancel_requested or stop_reason_text in {"cancelled", "canceled"}
                elapsed = time.perf_counter() - start
                success = not cancelled and stop_reason in _OK_STOP_REASONS and not client.errors
                output = client.get_output()
                logger.info(
                    "ACP [%s]: done in %.2fs stop_reason=%s chars=%d tool_calls=%d",
                    run_id,
                    elapsed,
                    stop_reason,
                    len(output),
                    client.tool_call_count,
                )
                return ACPRunResult(
                    success=success,
                    output=output,
                    output_char_count=client.output_char_count,
                    output_truncated=client.output_truncated,
                    stop_reason=stop_reason_text,
                    session_id=active_session_id,
                    tool_calls=client.tool_calls,
                    tool_call_count=client.tool_call_count,
                    elapsed_seconds=elapsed,
                    error="; ".join(client.errors) if client.errors else None,
                    cancelled=cancelled,
                )
        except FileNotFoundError:
            elapsed = time.perf_counter() - start
            logger.error("ACP [%s]: command not found: %s", run_id, self.command)
            return ACPRunResult(
                success=False,
                stop_reason="error",
                session_id=active_session_id,
                elapsed_seconds=elapsed,
                error=(
                    f"ACP command not found: {self.command!r}. "
                    "Ensure the agent CLI is installed and on PATH."
                ),
                cancelled=self._cancel_requested,
            )
        except TimeoutError:
            elapsed = time.perf_counter() - start
            logger.error("ACP [%s]: timed out after %.0fs", run_id, self.timeout)
            return ACPRunResult(
                success=False,
                output=client.get_output(),
                output_char_count=client.output_char_count,
                output_truncated=client.output_truncated,
                stop_reason="timeout",
                session_id=active_session_id,
                tool_calls=client.tool_calls,
                tool_call_count=client.tool_call_count,
                elapsed_seconds=elapsed,
                error=f"Timed out after {self.timeout}s",
                cancelled=self._cancel_requested,
            )
        except (ConnectionError, OSError) as exc:
            elapsed = time.perf_counter() - start
            logger.error("ACP [%s]: connection failed: %s", run_id, exc)
            return ACPRunResult(
                success=False,
                stop_reason="error",
                session_id=active_session_id,
                elapsed_seconds=elapsed,
                error=f"ACP connection failed ({self.host}:{self.port}): {exc}",
                cancelled=self._cancel_requested,
            )
        except Exception as exc:  # pragma: no cover - defensive
            elapsed = time.perf_counter() - start
            logger.exception("ACP [%s]: run failed: %s", run_id, exc)
            return ACPRunResult(
                success=False,
                output=client.get_output(),
                output_char_count=client.output_char_count,
                output_truncated=client.output_truncated,
                stop_reason="error",
                session_id=active_session_id,
                tool_calls=client.tool_calls,
                tool_call_count=client.tool_call_count,
                elapsed_seconds=elapsed,
                error=str(exc),
                cancelled=self._cancel_requested,
            )
        finally:
            client.close_output()
            self._active_conn = None
            self._active_session_id = None
