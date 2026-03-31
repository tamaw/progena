from __future__ import annotations

import os
import secrets
import socket
import subprocess
import sys
import time
from typing import Sequence
from urllib.error import URLError
from urllib.request import urlopen


DEFAULT_INDEX_URL = "https://pypi.org/simple"
DEFAULT_CACHE_IMAGE = "jonasal/devpi-server:latest"
DEFAULT_CONTAINER_NAME = "progenitor-pypi-cache"
DEFAULT_VOLUME_NAME = "progenitor-pypi-cache-data"
DEFAULT_BIND_ADDRESS = "0.0.0.0"
DEFAULT_PORT = "3141"


def env_value(name: str, default: str) -> str:
    value = os.getenv(name, "").strip()
    return value or default


def write_output(name: str, value: str) -> None:
    output_path = os.getenv("GITHUB_OUTPUT", "").strip()
    if not output_path:
        return
    with open(output_path, "a", encoding="utf-8") as handle:
        handle.write(f"{name}={value}\n")


def run(command: Sequence[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        text=True,
        capture_output=True,
        check=False,
    )
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit code {result.returncode}"
        raise RuntimeError(f"{' '.join(command)} failed: {detail}")
    return result


def docker_available() -> bool:
    try:
        run(["docker", "version"], check=True)
    except (FileNotFoundError, RuntimeError):
        return False
    return True


def detect_host_ip() -> str:
    configured = os.getenv("PYPI_CACHE_HOST", "").strip()
    if configured:
        return configured

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return ""


def container_state(container_name: str) -> str:
    result = run(
        ["docker", "inspect", "--format", "{{.State.Status}}", container_name],
        check=False,
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def ensure_container(
    *,
    container_name: str,
    volume_name: str,
    cache_image: str,
    bind_address: str,
    port: str,
) -> None:
    run(["docker", "volume", "create", volume_name], check=True)

    state = container_state(container_name)
    if state == "running":
        return
    if state:
        run(["docker", "start", container_name], check=True)
        return

    root_password = os.getenv("PYPI_CACHE_ROOT_PASSWORD", "").strip() or secrets.token_urlsafe(24)
    run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            container_name,
            "--restart",
            "unless-stopped",
            "--label",
            "com.progenitor.managed=true",
            "--label",
            "com.progenitor.component=python-package-cache",
            "-p",
            f"{bind_address}:{port}:3141",
            "-v",
            f"{volume_name}:/devpi/server",
            "-e",
            f"DEVPI_PASSWORD={root_password}",
            cache_image,
        ],
        check=True,
    )


def cache_ready(port: str, timeout_seconds: int = 90) -> bool:
    deadline = time.time() + timeout_seconds
    health_url = f"http://127.0.0.1:{port}/root/pypi/+simple/"
    while time.time() < deadline:
        try:
            with urlopen(health_url, timeout=5) as response:
                if 200 <= response.status < 500:
                    return True
        except URLError:
            pass
        time.sleep(2)
    return False


def emit_fallback(reason: str) -> int:
    fallback_index_url = env_value("PYPI_CACHE_FALLBACK_INDEX_URL", DEFAULT_INDEX_URL)
    print(f"::warning::{reason} Falling back to {fallback_index_url}.")
    write_output("index_url", fallback_index_url)
    write_output("trusted_host", "")
    write_output("cache_enabled", "false")
    write_output("cache_host", "")
    return 0


def main() -> int:
    if not docker_available():
        return emit_fallback("Docker is unavailable on this runner.")

    cache_image = env_value("PYPI_CACHE_IMAGE", DEFAULT_CACHE_IMAGE)
    container_name = env_value("PYPI_CACHE_CONTAINER_NAME", DEFAULT_CONTAINER_NAME)
    volume_name = env_value("PYPI_CACHE_VOLUME_NAME", DEFAULT_VOLUME_NAME)
    bind_address = env_value("PYPI_CACHE_BIND_ADDRESS", DEFAULT_BIND_ADDRESS)
    port = env_value("PYPI_CACHE_PORT", DEFAULT_PORT)
    host_ip = detect_host_ip()

    if not host_ip:
        return emit_fallback("Could not determine a host IP for the local Python package cache.")

    try:
        ensure_container(
            container_name=container_name,
            volume_name=volume_name,
            cache_image=cache_image,
            bind_address=bind_address,
            port=port,
        )
    except RuntimeError as exc:
        return emit_fallback(f"{exc}.")

    if not cache_ready(port):
        return emit_fallback(
            f"Local Python package cache container {container_name} did not become ready on port {port}."
        )

    index_url = f"http://{host_ip}:{port}/root/pypi/+simple/"
    print(f"Using local Python package cache at {index_url}")
    write_output("index_url", index_url)
    write_output("trusted_host", host_ip)
    write_output("cache_enabled", "true")
    write_output("cache_host", host_ip)
    return 0


if __name__ == "__main__":
    sys.exit(main())
