"""
Service-based Progenitor Client for Backend.

ARCH-004: Alternative to subprocess-based client.

This client uses the in-process ProgenitorService instead of
spawning a subprocess, providing:
- Direct async/await integration
- No subprocess management
- Proper error propagation
- Better streaming support
"""

import logging
from collections.abc import AsyncIterator
from typing import Any

from ..progenitor.config import ProgenitorConfig
from ..progenitor.service import ProgenitorService, get_service
from .config import ChatServerConfig

logger = logging.getLogger(__name__)


class ServiceProgenitorClient:
    """Client that uses in-process ProgenitorService.

    This is the recommended client for production use as it avoids
    all the complexity and failure modes of subprocess IPC.
    """

    def __init__(
        self,
        chat_config: ChatServerConfig,
        progenitor_config: ProgenitorConfig | None = None,
    ):
        """Initialize the service-based client.

        Args:
            chat_config: Backend configuration.
            progenitor_config: Optional Progenitor configuration.
        """
        self.chat_config = chat_config
        self.progenitor_config = progenitor_config
        self._service: ProgenitorService | None = None

    async def start(self) -> None:
        """Start the Progenitor service."""
        if self._service is not None and self._service.is_initialized:
            return

        logger.info("Starting Progenitor service...")
        self._service = get_service(self.progenitor_config)
        await self._service.initialize()
        logger.info("Progenitor service started")

    async def stop(self) -> None:
        """Stop the Progenitor service."""
        if self._service:
            await self._service.shutdown()
            self._service = None

    async def send_message(
        self,
        message: str,
        request_id: str | None = None,
    ) -> str:
        """Send a message and get the response.

        Args:
            message: The user message.
            request_id: Optional request ID for correlation.

        Returns:
            The assistant's response.
        """
        if not self._service or not self._service.is_initialized:
            await self.start()
        service = self._service
        if service is None:
            raise RuntimeError("Progenitor service failed to start")

        return await service.handle_message(message, request_id)

    async def send_message_stream(
        self,
        message: str,
        request_id: str | None = None,
    ) -> AsyncIterator[str]:
        """Send a message and stream the response.

        Args:
            message: The user message.
            request_id: Optional request ID for correlation.

        Yields:
            Response chunks.
        """
        if not self._service or not self._service.is_initialized:
            await self.start()
        service = self._service
        if service is None:
            raise RuntimeError("Progenitor service failed to start")

        async for chunk in service.handle_message_stream(message, request_id):
            yield chunk

    async def reset_conversation(
        self,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        """Reset the persisted conversation thread used by the service."""
        _ = request_id
        if not self._service or not self._service.is_initialized:
            await self.start()
        service = self._service
        if service is None:
            raise RuntimeError("Progenitor service failed to start")

        return await service.reset_conversation()

    @property
    def is_running(self) -> bool:
        """Check if the service is running."""
        return self._service is not None and self._service.is_initialized

    def last_heartbeat(self) -> float | None:
        """Get last heartbeat timestamp (for compatibility)."""
        # In-process service doesn't need heartbeats
        import time

        return time.time() if self.is_running else None

    def queue_depth(self) -> int:
        """Get queue depth (for compatibility)."""
        # In-process service processes directly
        return 0


def get_service_client(
    chat_config: ChatServerConfig,
    progenitor_config: ProgenitorConfig | None = None,
) -> ServiceProgenitorClient:
    """Create a service-based Progenitor client.

    Args:
        chat_config: Backend configuration.
        progenitor_config: Optional Progenitor configuration.

    Returns:
        A ServiceProgenitorClient instance.
    """
    return ServiceProgenitorClient(chat_config, progenitor_config)
