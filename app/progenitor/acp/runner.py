"""Generic ACP runner: spawn an ACP agent over stdio and run one prompt.

``ACPRunner`` is the agent-agnostic equivalent of the Builder Agent's
``CopilotACPManager`` (``app/agents/builder_agent/runtime/copilot_acp.py``). It
connects to an ACP agent — either by spawning ``command *args`` as a local stdio
subprocess, or by connecting over **TCP** to an already-running server (e.g.
``copilot --acp --port 3000``) — performs the ACP handshake (``initialize`` →
``session/new`` → ``session/prompt``), collects the streamed output via
:class:`ACPSessionClient`, and returns a normalised result.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

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
    stop_reason: str | None = None
    session_id: str | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    elapsed_seconds: float = 0.0
    error: str | None = None
    cancelled: bool = False


class ACPRunner:
    """Run one prompt against an ACP agent over stdio (spawned) or TCP (connect).

    - **stdio** (default): spawn ``command *args`` as a subprocess and drive it.
    - **TCP**: set ``port`` (and optionally ``host``) to connect to an already
      running ACP server (e.g. ``copilot --acp --port 3000``); ``model`` is then
      applied per session via ``set_session_model``.
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

    async def _set_session_model(
        self,
        conn: Any,
        initialized: Any,
        session_id: str,
    ) -> None:
        """Select a model, using Claude ACP's untyped config-option extension."""
        if not self.model:
            return
        capabilities = getattr(initialized, "agent_capabilities", None)
        session_capabilities = getattr(capabilities, "session_capabilities", None)
        typed_models = getattr(session_capabilities, "models", None) or getattr(
            capabilities, "models", None
        )
        if typed_models:
            await conn.set_session_model(model_id=self.model, session_id=session_id)
            return
        raw_connection = getattr(conn, "_conn", None)
        send_request = getattr(raw_connection, "send_request", None)
        if not callable(send_request):
            logger.warning("ACP agent does not expose a model-selection API")
            return
        await cast(
            Any,
            send_request(
                "session/set_config_option",
                {
                    "sessionId": session_id,
                    "configId": "model",
                    "value": self.model,
                },
            ),
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
            conn = connect_to_agent(client, writer, reader)  # input=writer, output=reader
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
            ) as (conn, proc):
                logger.info("ACP [%s]: process pid=%s", run_id, getattr(proc, "pid", "?"))
                yield conn

    async def run(
        self,
        prompt_text: str,
        cwd: Path | str,
        *,
        run_id: str = "unknown",
        session_id: str | None = None,
        on_progress: Callable[[dict[str, Any]], Awaitable[None] | None] | None = None,
    ) -> ACPRunResult:
        """Run one prompt, resuming ``session_id`` when a prior turn exists."""
        cwd_path = Path(cwd)
        start = time.perf_counter()
        client = ACPSessionClient(run_id=run_id, on_progress=on_progress)
        active_session_id = session_id

        try:
            async with self._open(client, cwd_path, run_id) as conn:
                initialized = await conn.initialize(protocol_version=PROTOCOL_VERSION)
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
                if on_progress is not None:
                    progress_result = on_progress(
                        {"kind": "session", "session_id": active_session_id}
                    )
                    if asyncio.iscoroutine(progress_result):
                        await progress_result
                if self.is_tcp and self.model:
                    await self._set_session_model(conn, initialized, active_session_id)
                response = await asyncio.wait_for(
                    conn.prompt(prompt=[text_block(prompt_text)], session_id=active_session_id),
                    timeout=self.timeout,
                )
                stop_reason = getattr(response, "stop_reason", None)
                stop_reason_text = str(stop_reason) if stop_reason is not None else None
                cancelled = self._cancel_requested or stop_reason_text in {"cancelled", "canceled"}
                elapsed = time.perf_counter() - start
                success = not cancelled and stop_reason in _OK_STOP_REASONS and not client.errors
                logger.info(
                    "ACP [%s]: done in %.2fs stop_reason=%s chars=%d tool_calls=%d",
                    run_id,
                    elapsed,
                    stop_reason,
                    len(client.get_output()),
                    len(client.tool_calls),
                )
                return ACPRunResult(
                    success=success,
                    output=client.get_output(),
                    stop_reason=stop_reason_text,
                    session_id=active_session_id,
                    tool_calls=client.tool_calls,
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
                stop_reason="timeout",
                session_id=active_session_id,
                tool_calls=client.tool_calls,
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
                stop_reason="error",
                session_id=active_session_id,
                tool_calls=client.tool_calls,
                elapsed_seconds=elapsed,
                error=str(exc),
                cancelled=self._cancel_requested,
            )
        finally:
            self._active_conn = None
            self._active_session_id = None
