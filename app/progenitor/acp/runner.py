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
from collections.abc import AsyncIterator, Sequence
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
    stop_reason: str | None = None
    session_id: str | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    elapsed_seconds: float = 0.0
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "output": self.output,
            "stop_reason": self.stop_reason,
            "session_id": self.session_id,
            "tool_calls": self.tool_calls,
            "elapsed_seconds": self.elapsed_seconds,
            "error": self.error,
        }


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
    ) -> ACPRunResult:
        """Connect to the agent, send one prompt, and return the collected result.

        Never raises for protocol/agent/connection failures — they are reported on
        the returned :class:`ACPRunResult` (``success=False`` with ``error``).
        """
        cwd_path = Path(cwd)
        start = time.perf_counter()
        client = ACPSessionClient(run_id=run_id)

        try:
            async with self._open(client, cwd_path, run_id) as conn:
                await conn.initialize(protocol_version=PROTOCOL_VERSION)
                session = await conn.new_session(cwd=str(cwd_path), mcp_servers=[])
                session_id = session.session_id
                # In TCP mode the server is shared/long-lived, so select the model
                # per session rather than via a launch flag.
                if self.is_tcp and self.model:
                    with contextlib.suppress(Exception):
                        await conn.set_session_model(model_id=self.model, session_id=session_id)
                response = await asyncio.wait_for(
                    conn.prompt(prompt=[text_block(prompt_text)], session_id=session_id),
                    timeout=self.timeout,
                )
                stop_reason = getattr(response, "stop_reason", None)
                elapsed = time.perf_counter() - start
                success = stop_reason in _OK_STOP_REASONS and not client.errors
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
                    stop_reason=str(stop_reason) if stop_reason is not None else None,
                    session_id=session_id,
                    tool_calls=client.tool_calls,
                    elapsed_seconds=elapsed,
                    error="; ".join(client.errors) if client.errors else None,
                )
        except FileNotFoundError:
            elapsed = time.perf_counter() - start
            logger.error("ACP [%s]: command not found: %s", run_id, self.command)
            return ACPRunResult(
                success=False,
                stop_reason="error",
                elapsed_seconds=elapsed,
                error=(
                    f"ACP command not found: {self.command!r}. "
                    "Ensure the agent CLI is installed and on PATH."
                ),
            )
        except TimeoutError:
            elapsed = time.perf_counter() - start
            logger.error("ACP [%s]: timed out after %.0fs", run_id, self.timeout)
            return ACPRunResult(
                success=False,
                output=client.get_output(),
                stop_reason="timeout",
                tool_calls=client.tool_calls,
                elapsed_seconds=elapsed,
                error=f"Timed out after {self.timeout}s",
            )
        except (ConnectionError, OSError) as exc:
            elapsed = time.perf_counter() - start
            logger.error("ACP [%s]: connection failed: %s", run_id, exc)
            return ACPRunResult(
                success=False,
                stop_reason="error",
                elapsed_seconds=elapsed,
                error=f"ACP connection failed ({self.host}:{self.port}): {exc}",
            )
        except Exception as exc:  # pragma: no cover - defensive
            elapsed = time.perf_counter() - start
            logger.exception("ACP [%s]: run failed: %s", run_id, exc)
            return ACPRunResult(
                success=False,
                stop_reason="error",
                elapsed_seconds=elapsed,
                error=str(exc),
            )
