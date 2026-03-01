"""
Progenitor Service - In-process async service for direct integration.

ARCH-004: Replace subprocess IPC with direct async calls.

This provides an in-process Progenitor service that can be used by the
backend without subprocess communication, eliminating:
- Deadlock risks from pipe buffering
- JSON parsing errors
- Zombie processes
- Unstructured error handling

Usage:
    service = ProgenitorService(config)
    await service.initialize()

    response = await service.handle_message("Hello")
    async for chunk in service.handle_message_stream("Hello"):
        print(chunk)
"""

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from .config import ProgenitorConfig, get_config
from .llm.client import LLMClient
from .runtime.deep_agent_adapter import create_deep_agent_for_loop
from .state.manager import StateManager
from .state.models import ConversationMessage, ProgenitorState
from .tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class ProgenitorService:
    """In-process Progenitor service for direct async calls.

    This eliminates the need for subprocess IPC by running Progenitor
    directly within the same process as the backend.

    Benefits:
    - No subprocess management overhead
    - Direct async/await integration
    - Proper error propagation
    - Structured streaming support
    - Shared memory for state (optional)
    """

    def __init__(
        self,
        config: ProgenitorConfig | None = None,
        project_root: Path | None = None,
    ):
        """Initialize the Progenitor service.

        Args:
            config: Progenitor configuration. If None, loads from environment.
            project_root: Project root path. If None, uses default.
        """
        self.project_root = project_root or Path(__file__).parent.parent.parent
        self.config = config or get_config(
            self.project_root,
            self.project_root / "config"
        )

        self._initialized = False
        self._lock = asyncio.Lock()

        # Components (initialized in initialize())
        self.state_manager: StateManager | None = None
        self.llm_client: LLMClient | None = None
        self.tool_registry: ToolRegistry | None = None
        self.graph: Any = None

        # State tracking
        self._session_id: str | None = None
        self._message_count = 0

    async def initialize(self) -> None:
        """Initialize all service components."""
        if self._initialized:
            return

        async with self._lock:
            if self._initialized:
                return

            logger.info("Initializing Progenitor service...")

            # Ensure directories exist
            for dir_path in [
                self.config.storage.state_dir,
                self.config.storage.state_dir.parent / "config",
                self.config.storage.log_dir,
                self.config.storage.worktree_dir,
            ]:
                dir_path.mkdir(parents=True, exist_ok=True)

            # Initialize state manager
            self.state_manager = StateManager(
                self.config.storage.state_dir,
                self.config.storage.log_dir,
                lock_timeout=self.config.storage.state_lock_timeout_seconds,
            )
            await self.state_manager.initialize()

            # Initialize LLM client
            self.llm_client = LLMClient(config=self.config.llm, mcp_config=self.config.mcp)

            # Initialize tool registry
            self.tool_registry = ToolRegistry(
                self.config.storage.state_dir.parent / "config" / "agents.json"
            )
            await self.tool_registry.load()

            # Initialize MCP tools and register them so they show up
            # in tool discovery and can be invoked via the registry.
            from .tools.mcp import setup_mcp_for_llm_client

            await setup_mcp_for_llm_client(self.llm_client, self.config.mcp)
            await self._register_mcp_tools()

            # Run agent discovery to populate registry from agents/ folder
            await self._discover_agents()

            # Create the LangGraph
            self.graph = create_deep_agent_for_loop(
                config=self.config,
                tool_registry=self.tool_registry,
                llm_client=self.llm_client,
                state_manager=self.state_manager,
                project_root=str(self.project_root),
                log_dir=self.config.storage.log_dir,
            )

            # Generate session ID
            self._session_id = f"session_{uuid.uuid4().hex[:12]}"

            self._initialized = True
            logger.info(
                "Progenitor service initialized (session=%s)",
                self._session_id
            )

    async def _register_mcp_tools(self) -> None:
        """Register MCP tools discovered via configured MCP endpoint."""
        if not self.llm_client or not self.tool_registry:
            return

        if not self.llm_client.mcp_tools:
            return

        for tool in self.llm_client.get_mcp_tool_registry_entries():
            await self.tool_registry.register_tool(tool)

    async def handle_message(
        self,
        message: str,
        request_id: str | None = None,
    ) -> str:
        """Handle a user message and return the response.

        Args:
            message: The user message to process.
            request_id: Optional request ID for correlation.

        Returns:
            The assistant's response.

        Raises:
            RuntimeError: If service is not initialized.
        """
        if not self._initialized:
            await self.initialize()

        request_id = request_id or f"req_{uuid.uuid4().hex[:12]}"
        self._message_count += 1

        logger.info(
            "Processing message (request_id=%s, count=%d)",
            request_id,
            self._message_count,
        )

        # Build initial state
        initial_state: ProgenitorState = {
            "current_input": message,
            "messages": [],
            "cycle_count": 0,
            "actions_this_cycle": 0,
            "shutdown_requested": False,
        }

        # Invoke the graph
        config = {"configurable": {"thread_id": request_id}}

        try:
            if self.graph is None:
                raise RuntimeError("Graph not initialized")
            result = await self.graph.ainvoke(initial_state, config)

            # Extract response
            response = result.get("pending_response", "")

            # Log conversation
            if self.state_manager:
                await self.state_manager.log_conversation(
                    ConversationMessage(
                        id=f"msg_{uuid.uuid4().hex[:8]}",
                        role="user",
                        content=message,
                        metadata={"request_id": request_id},
                    )
                )
                if response:
                    await self.state_manager.log_conversation(
                        ConversationMessage(
                            id=f"msg_{uuid.uuid4().hex[:8]}",
                            role="assistant",
                            content=response,
                            metadata={"request_id": request_id},
                        )
                    )

            return response or "I processed your request but have no response."

        except Exception as e:
            logger.error(
                "Error processing message (request_id=%s): %s",
                request_id,
                e,
                exc_info=True,
            )
            raise

    async def handle_message_stream(
        self,
        message: str,
        request_id: str | None = None,
    ) -> AsyncIterator[str]:
        """Handle a message and stream the response.

        Args:
            message: The user message to process.
            request_id: Optional request ID for correlation.

        Yields:
            Response chunks as they become available.
        """
        if not self._initialized:
            await self.initialize()

        request_id = request_id or f"req_{uuid.uuid4().hex[:12]}"
        self._message_count += 1

        logger.info(
            "Processing streaming message (request_id=%s)",
            request_id,
        )

        # Build initial state
        initial_state: ProgenitorState = {
            "current_input": message,
            "messages": [],
            "cycle_count": 0,
            "actions_this_cycle": 0,
            "shutdown_requested": False,
        }

        config = {"configurable": {"thread_id": request_id}}

        try:
            if self.graph is None:
                raise RuntimeError("Graph not initialized")
            # Use astream for streaming
            full_response = ""
            async for event in self.graph.astream(initial_state, config):
                # Extract any response updates
                for _node_name, node_output in event.items():
                    if isinstance(node_output, dict) and (chunk := node_output.get("pending_response")):
                        # Yield new content
                        new_content = chunk[len(full_response):]
                        if new_content:
                            full_response = chunk
                            yield new_content

            # Log conversation
            if self.state_manager:
                await self.state_manager.log_conversation(
                    ConversationMessage(
                        id=f"msg_{uuid.uuid4().hex[:8]}",
                        role="user",
                        content=message,
                        metadata={"request_id": request_id},
                    )
                )
                if full_response:
                    await self.state_manager.log_conversation(
                        ConversationMessage(
                            id=f"msg_{uuid.uuid4().hex[:8]}",
                            role="assistant",
                            content=full_response,
                            metadata={"request_id": request_id},
                        )
                    )

        except Exception as e:
            logger.error(
                "Error in streaming (request_id=%s): %s",
                request_id,
                e,
                exc_info=True,
            )
            raise

    @property
    def is_initialized(self) -> bool:
        """Check if the service is initialized."""
        return self._initialized

    @property
    def session_id(self) -> str | None:
        """Get the current session ID."""
        return self._session_id

    @property
    def message_count(self) -> int:
        """Get the number of messages processed."""
        return self._message_count

    async def get_health(self) -> dict[str, Any]:
        """Get service health information."""
        return {
            "initialized": self._initialized,
            "session_id": self._session_id,
            "message_count": self._message_count,
            "state_manager": self.state_manager is not None,
            "llm_client": self.llm_client is not None,
            "graph": self.graph is not None,
        }

    async def shutdown(self) -> None:
        """Gracefully shutdown the service."""
        logger.info("Shutting down Progenitor service...")

        # Commit any pending state
        if self.state_manager:
            await self.state_manager.commit_state()

        self._initialized = False
        logger.info("Progenitor service shutdown complete")

    async def _discover_agents(self) -> None:
        """Discover and register agents from the agents/ folder."""
        try:
            from pathlib import Path

            from .tools.agent_discovery import discover_agents

            # Determine agents directory relative to project root
            # Agents are built/deployed from app/agents/
            project_root = Path(__file__).parent.parent.parent
            agents_dir = project_root / "app" / "agents"

            if not agents_dir.exists():
                logger.warning("Agents directory not found: %s", agents_dir)
                return

            if not self.llm_client or not self.tool_registry:
                logger.warning("LLM client or registry not available for discovery")
                return

            logger.info("Discovering agents from %s", agents_dir)
            result = await discover_agents(
                agents_dir=agents_dir,
                llm_client=self.llm_client,
                registry=self.tool_registry,
            )

            if result.get("agents_registered", 0) > 0:
                logger.info(
                    "Agent discovery complete: %d agents registered",
                    result["agents_registered"],
                )
            elif result.get("agents_found", 0) == 0:
                logger.info("No agents found in %s", agents_dir)
            else:
                logger.warning("Agent discovery issues: %s", result.get("errors"))

        except Exception as e:
            logger.error("Agent discovery failed: %s", e, exc_info=True)
            # Non-fatal: continue with whatever was in registry

    async def rediscover_agents(self) -> dict[str, Any]:
        """Re-run agent discovery. Can be invoked as a tool."""
        if not self._initialized:
            await self.initialize()
            return {"message": "Service was not initialized, ran full initialization"}

        await self._discover_agents()
        return {"message": "Agent rediscovery complete"}


# Module-level service instance for singleton access
_service: ProgenitorService | None = None


def get_service(config: ProgenitorConfig | None = None) -> ProgenitorService:
    """Get or create the global Progenitor service instance.

    Args:
        config: Optional configuration. Only used when creating new instance.

    Returns:
        The global ProgenitorService instance.
    """
    global _service
    if _service is None:
        _service = ProgenitorService(config)
    return _service


async def get_initialized_service(
    config: ProgenitorConfig | None = None
) -> ProgenitorService:
    """Get an initialized service instance.

    Args:
        config: Optional configuration.

    Returns:
        An initialized ProgenitorService instance.
    """
    service = get_service(config)
    if not service.is_initialized:
        await service.initialize()
    return service
