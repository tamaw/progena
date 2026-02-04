"""
Port Utilities - Reliable port allocation and availability checking.

Provides utilities to:
- Check if a port is available (not bound by any process)
- Check if a service is running on a port (health check)
- Find the next available port in a range
"""

import asyncio
import logging
import socket
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


def is_port_available(port: int, host: str = "localhost") -> bool:
    """
    Check if a port is available for binding.

    This checks at the OS level whether the port is free.

    Args:
        port: Port number to check.
        host: Host to check on (default: localhost).

    Returns:
        True if port is available, False if in use.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.settimeout(1)
            sock.bind((host, port))
            return True
    except (OSError, socket.error) as e:
        logger.debug(f"Port {port} is not available: {e}")
        return False


async def is_service_running_on_port(
    port: int,
    host: str = "localhost",
    health_path: str = "/health",
    timeout: float = 2.0,
) -> bool:
    """
    Check if a service is already running on a port via health endpoint.

    This is useful to detect if another agent/service is already listening.

    Args:
        port: Port number to check.
        host: Host to check on (default: localhost).
        health_path: Path to health endpoint (default: /health).
        timeout: Request timeout in seconds.

    Returns:
        True if a service responds on the health endpoint, False otherwise.
    """
    url = f"http://{host}:{port}{health_path}"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url)
            # Any response means something is running
            logger.debug(f"Service running on port {port}: status {resp.status_code}")
            return True
    except (httpx.ConnectError, httpx.TimeoutException):
        return False
    except Exception as e:
        logger.debug(f"Error checking service on port {port}: {e}")
        return False


def find_available_port(
    start_port: int = 8080,
    end_port: int = 8199,
    host: str = "localhost",
    exclude_ports: Optional[set[int]] = None,
) -> int:
    """
    Find an available port in the given range.

    Checks ports sequentially until finding one that is both:
    - Not in the exclude_ports set
    - Not bound at the OS level

    Args:
        start_port: Start of port range (inclusive).
        end_port: End of port range (inclusive).
        host: Host to check on (default: localhost).
        exclude_ports: Set of ports to skip (e.g., already allocated).

    Returns:
        An available port number.

    Raises:
        RuntimeError: If no ports are available in the range.
    """
    exclude = exclude_ports or set()

    for port in range(start_port, end_port + 1):
        if port in exclude:
            logger.debug(f"Port {port} is in exclude list, skipping")
            continue

        if is_port_available(port, host):
            logger.info(f"Found available port: {port}")
            return port
        else:
            logger.debug(f"Port {port} is not available")

    raise RuntimeError(
        f"No available ports in range {start_port}-{end_port}"
    )


async def find_available_port_async(
    start_port: int = 8080,
    end_port: int = 8199,
    host: str = "localhost",
    exclude_ports: Optional[set[int]] = None,
    check_health: bool = False,
) -> int:
    """
    Find an available port in the given range (async version).

    Similar to find_available_port but can optionally also check
    if a service is running via health endpoint.

    Args:
        start_port: Start of port range (inclusive).
        end_port: End of port range (inclusive).
        host: Host to check on (default: localhost).
        exclude_ports: Set of ports to skip.
        check_health: Also check health endpoint (slower but more thorough).

    Returns:
        An available port number.

    Raises:
        RuntimeError: If no ports are available in the range.
    """
    exclude = exclude_ports or set()

    for port in range(start_port, end_port + 1):
        if port in exclude:
            continue

        # First check OS-level availability (fast)
        if not is_port_available(port, host):
            continue

        # Optionally check if a service responds (catches edge cases)
        if check_health:
            if await is_service_running_on_port(port, host):
                logger.debug(f"Port {port} has a service running, skipping")
                continue

        logger.info(f"Found available port: {port}")
        return port

    raise RuntimeError(
        f"No available ports in range {start_port}-{end_port}"
    )


# Default port range for A2A agents
DEFAULT_AGENT_PORT_START = 8080
DEFAULT_AGENT_PORT_END = 8199
