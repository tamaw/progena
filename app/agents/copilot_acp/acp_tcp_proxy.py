"""TCP-to-stdio bridge for GitHub Copilot ACP.

Accepts TCP connections on a configurable port and spawns a Copilot CLI
process per connection, bridging TCP bytes ↔ copilot stdin/stdout
bidirectionally.  A lightweight HTTP health-check runs on a separate port.

Usage:
    python -m app.agents.copilot_acp.acp_tcp_proxy
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

logger = logging.getLogger("copilot_acp.proxy")

ACP_PORT = int(os.getenv("COPILOT_ACP_PORT", "3000"))
HEALTH_PORT = int(os.getenv("COPILOT_ACP_HEALTH_PORT", "3001"))
COPILOT_BIN = os.getenv("PROGENITOR_COPILOT_BIN", "copilot")
COPILOT_MODEL = os.getenv("PROGENITOR_COPILOT_MODEL", "gpt-5.3-codex")
COPILOT_FLAGS = os.getenv("PROGENITOR_COPILOT_FLAGS", "--yolo").split()


async def _bridge(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    label: str,
) -> None:
    """Copy bytes from *reader* to *writer* until EOF."""
    try:
        while True:
            chunk = await reader.read(8192)
            if not chunk:
                break
            writer.write(chunk)
            await writer.drain()
    except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError):
        pass
    except Exception:
        logger.exception("bridge %s error", label)
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


async def _handle_connection(
    tcp_reader: asyncio.StreamReader,
    tcp_writer: asyncio.StreamWriter,
) -> None:
    """Handle a single TCP connection by spawning a copilot ACP process."""
    peer = tcp_writer.get_extra_info("peername")
    logger.info("New connection from %s", peer)

    cmd = [COPILOT_BIN, "--acp", *COPILOT_FLAGS, "--model", COPILOT_MODEL]
    logger.info("Spawning: %s", " ".join(cmd))

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except Exception:
        logger.exception("Failed to spawn copilot process")
        tcp_writer.close()
        await tcp_writer.wait_closed()
        return

    assert proc.stdin is not None
    assert proc.stdout is not None
    assert proc.stderr is not None

    # Bridge TCP ↔ copilot stdin/stdout
    tcp_to_copilot = asyncio.create_task(
        _bridge(tcp_reader, proc.stdin, f"tcp→copilot({peer})"),
    )
    copilot_to_tcp = asyncio.create_task(
        _bridge(proc.stdout, tcp_writer, f"copilot→tcp({peer})"),
    )

    # Log stderr in background
    async def _drain_stderr() -> None:
        assert proc.stderr is not None
        while True:
            line = await proc.stderr.readline()
            if not line:
                break
            logger.debug("copilot stderr: %s", line.decode(errors="replace").rstrip())

    stderr_task = asyncio.create_task(_drain_stderr())

    try:
        done, pending = await asyncio.wait(
            [tcp_to_copilot, copilot_to_tcp],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
    finally:
        if proc.returncode is None:
            try:
                proc.terminate()
                await asyncio.wait_for(proc.wait(), timeout=5)
            except (TimeoutError, ProcessLookupError):
                proc.kill()
        stderr_task.cancel()
        logger.info("Connection from %s closed (copilot exit=%s)", peer, proc.returncode)


# ---------------------------------------------------------------------------
# Health check (minimal HTTP on HEALTH_PORT)
# ---------------------------------------------------------------------------

_HEALTH_RESPONSE = (
    b"HTTP/1.1 200 OK\r\n"
    b"Content-Type: application/json\r\n"
    b"Content-Length: 25\r\n"
    b"\r\n"
    b'{"status":"healthy"}\n'
)


async def _handle_health(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
) -> None:
    try:
        await reader.read(4096)  # consume request
        writer.write(_HEALTH_RESPONSE)
        await writer.drain()
    except Exception:
        pass
    finally:
        writer.close()
        await writer.wait_closed()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> None:
    logging.basicConfig(
        level=logging.DEBUG if os.getenv("DEBUG") else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stdout,
    )

    logger.info(
        "Starting Copilot ACP TCP proxy (acp_port=%d, health_port=%d, model=%s)",
        ACP_PORT,
        HEALTH_PORT,
        COPILOT_MODEL,
    )

    acp_server = await asyncio.start_server(_handle_connection, "0.0.0.0", ACP_PORT)
    health_server = await asyncio.start_server(_handle_health, "0.0.0.0", HEALTH_PORT)

    logger.info("Listening on :%d (ACP) and :%d (health)", ACP_PORT, HEALTH_PORT)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass  # Windows

    async with acp_server, health_server:
        await stop.wait()

    logger.info("Shutting down")


if __name__ == "__main__":
    asyncio.run(main())
