"""
Container runner entrypoint used inside progenitor runner images.

Contract:
- --mode=a2a|mcp
- --config=/workspace/run.json

The runner starts the configured command in stdio or HTTP mode and proxies
stdio where needed.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
from pathlib import Path
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)


def _configure_logging() -> None:
    logging.basicConfig(
        level=os.getenv("PROGENITOR_RUNNER_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )


def _load_config(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _build_child_env(config: dict[str, Any]) -> dict[str, str]:
    env = os.environ.copy()
    env.update(config.get("env", {}))
    return env


async def _await_http_ready(
    port: int,
    path: str,
    timeout_seconds: float,
) -> None:
    endpoint = f"http://127.0.0.1:{port}{path}"
    timeout_at = asyncio.get_event_loop().time() + timeout_seconds

    async with aiohttp.ClientSession() as session:
        while True:
            if asyncio.get_event_loop().time() > timeout_at:
                raise TimeoutError(f"HTTP readiness timeout for {endpoint}")
            try:
                async with session.get(endpoint, timeout=aiohttp.ClientTimeout(total=2)) as response:
                    if response.status < 500:
                        return
            except (aiohttp.ClientError, TimeoutError):
                pass
            await asyncio.sleep(0.25)


async def _run_stdio(command: list[str], cwd: str, env: dict[str, str]) -> int:
    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=cwd,
        env=env,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    stdin = process.stdin
    stdout = process.stdout
    assert stdin is not None
    assert stdout is not None

    async def pump_stdin() -> None:
        loop = asyncio.get_event_loop()
        while True:
            chunk = await loop.run_in_executor(None, os.read, 0, 65536)
            if not chunk:
                with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                    stdin.close()
                return
            stdin.write(chunk)
            await stdin.drain()

    async def pump_stdout() -> None:
        while True:
            chunk = await stdout.read(65536)
            if not chunk:
                return
            os.write(1, chunk)

    async def pump_stderr() -> None:
        assert process.stderr is not None
        while True:
            chunk = await process.stderr.read(65536)
            if not chunk:
                return
            os.write(2, chunk)

    await asyncio.gather(pump_stdin(), pump_stdout(), pump_stderr())
    return await process.wait()


async def _run_http(
    command: list[str],
    cwd: str,
    env: dict[str, str],
    port: int,
    readiness_path: str,
    timeout_seconds: float,
) -> int:
    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=cwd,
        env=env,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    stdout = process.stdout
    stderr = process.stderr
    assert stdout is not None
    assert stderr is not None

    async def pump_stdout() -> None:
        while True:
            chunk = await stdout.read(65536)
            if not chunk:
                return
            os.write(1, chunk)

    async def pump_stderr() -> None:
        while True:
            chunk = await stderr.read(65536)
            if not chunk:
                return
            os.write(2, chunk)

    stdout_task = asyncio.create_task(pump_stdout())
    stderr_task = asyncio.create_task(pump_stderr())

    try:
        await _await_http_ready(port=port, path=readiness_path, timeout_seconds=timeout_seconds)
        logger.info("RUNNER_READY mode=http port=%d", port)
    except Exception:
        process.terminate()
        await process.wait()
        raise

    await process.wait()
    await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
    return process.returncode or 0


async def _main_async(mode: str, config_path: str) -> int:
    config = _load_config(config_path)
    command = [*config.get("command", []), *config.get("args", [])]
    if not command:
        raise ValueError("run.json requires non-empty 'command'")

    cwd = config.get("cwd") or "/workspace"
    env = _build_child_env(config)
    transport = config.get("transport", "stdio")

    logger.info("Starting runner mode=%s transport=%s command=%s", mode, transport, command)

    if transport == "stdio":
        logger.info("RUNNER_READY mode=stdio")
        return await _run_stdio(command=command, cwd=cwd, env=env)

    if transport == "http":
        ports = config.get("http", {}).get("ports", [])
        if not ports:
            raise ValueError("HTTP transport requires http.ports in run.json")
        container_port = int(ports[0]["container"])
        readiness_path = config.get("http", {}).get("readiness_path", "/")
        timeout_seconds = float(config.get("timeout_seconds", 30.0))
        return await _run_http(
            command=command,
            cwd=cwd,
            env=env,
            port=container_port,
            readiness_path=readiness_path,
            timeout_seconds=timeout_seconds,
        )

    raise ValueError(f"Unknown transport: {transport}")


def main() -> int:
    _configure_logging()

    parser = argparse.ArgumentParser(description="Progenitor isolated runner")
    parser.add_argument("--mode", required=True, choices=["a2a", "mcp"])
    parser.add_argument("--config", required=True)
    args = parser.parse_args()

    config_path = Path(args.config)
    if not config_path.exists():
        raise FileNotFoundError(f"Config not found: {config_path}")

    return asyncio.run(_main_async(mode=args.mode, config_path=str(config_path)))


if __name__ == "__main__":
    raise SystemExit(main())
