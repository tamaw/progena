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
        async for event in service.handle_message_stream_events("Hello"):
            print(event)
"""

import asyncio
import inspect
import logging
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, TypedDict

from langchain_core.messages import AIMessageChunk, HumanMessage

from .config import ProgenitorConfig, get_config
from .llm.client import LLMClient
from .runtime.deep_agent_adapter import create_deep_agent_for_loop
from .state.manager import StateManager
from .stream_events import (
    build_reasoning_delta_event,
    build_reasoning_summary_event,
    normalize_custom_event,
)
from .streaming_utils import (
    extract_reasoning_summary_text,
    extract_reasoning_token_count,
    extract_stream_chunk_text,
    extract_stream_reasoning_deltas,
    make_json_safe,
)
from .tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class ResponseDetails(TypedDict):
    """Normalized non-stream response payload returned from the in-process service."""

    response: str
    reasoning: str | None
    reasoning_tokens: int | None


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
        self.config = config or get_config(self.project_root, self.project_root / "config")

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
            self.llm_client = LLMClient(
                config=self.config.llm,
                mcp_config=self.config.mcp,
                prompts_dir=self.config.storage.prompts_dir,
            )

            # Initialize tool registry
            self.tool_registry = ToolRegistry(
                self.config.storage.state_dir.parent / "config" / "agents.json"
            )
            await self.tool_registry.load()

            # Initialize external MCP servers and cache their discovered tools.
            from .tools.mcp import refresh_mcp_runtime

            await refresh_mcp_runtime(self.llm_client, self.tool_registry, self.config.mcp)

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
            logger.info("Progenitor service initialized (session=%s)", self._session_id)

    async def handle_message(
        self,
        message: str,
        request_id: str | None = None,
        metadata: dict[str, Any] | None = None,
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

        try:
            if self.graph is None:
                raise RuntimeError("Graph not initialized")
            thread_id = await self._get_thread_id(metadata=metadata)
            graph_input = self._build_graph_input(message)
            config = {"configurable": {"thread_id": thread_id}}
            result = await self.graph.ainvoke(graph_input, config)
            response_details = self._extract_response_details(result)
            return response_details["response"]

        except Exception as e:
            logger.error(
                "Error processing message (request_id=%s): %s",
                request_id,
                e,
                exc_info=True,
            )
            raise

    async def handle_message_response(
        self,
        message: str,
        request_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ResponseDetails:
        """Handle a user message and return response text plus reasoning details."""
        if not self._initialized:
            await self.initialize()

        request_id = request_id or f"req_{uuid.uuid4().hex[:12]}"
        self._message_count += 1

        logger.info(
            "Processing rich response message (request_id=%s, count=%d)",
            request_id,
            self._message_count,
        )

        try:
            if self.graph is None:
                raise RuntimeError("Graph not initialized")
            thread_id = await self._get_thread_id(metadata=metadata)
            graph_input = self._build_graph_input(message)
            config = {"configurable": {"thread_id": thread_id}}
            result = await self.graph.ainvoke(graph_input, config)
            return self._extract_response_details(result)
        except Exception as e:
            logger.error(
                "Error processing rich response message (request_id=%s): %s",
                request_id,
                e,
                exc_info=True,
            )
            raise

    async def handle_message_stream_events(
        self,
        message: str,
        request_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Handle a message and yield structured streaming events."""
        if not self._initialized:
            await self.initialize()

        request_id = request_id or f"req_{uuid.uuid4().hex[:12]}"
        self._message_count += 1

        logger.info(
            "Processing streaming message (request_id=%s)",
            request_id,
        )

        try:
            if self.graph is None:
                raise RuntimeError("Graph not initialized")
            thread_id = await self._get_thread_id(metadata=metadata)
            graph_input = self._build_graph_input(message)
            config = {"configurable": {"thread_id": thread_id}}
            astream_kwargs: dict[str, Any] = {"config": config}
            if self._graph_accepts_stream_mode():
                astream_kwargs["stream_mode"] = ["messages", "updates", "custom"]
            full_response = ""
            last_result: dict[str, Any] = {}
            async for stream_item in self.graph.astream(graph_input, **astream_kwargs):
                mode = "updates"
                event = stream_item
                if isinstance(stream_item, tuple) and len(stream_item) == 2:
                    mode, event = stream_item

                if mode == "messages":
                    stream_events = self._build_message_stream_events(event, request_id=request_id)
                    if not stream_events:
                        continue
                    for stream_event in stream_events:
                        if stream_event["event_type"] == "message_delta":
                            chunk_text = str(stream_event["content"])
                            if chunk_text:
                                full_response += chunk_text
                        yield stream_event
                        if (
                            stream_event["event_type"] == "message_delta"
                            and stream_event["content"]
                        ):
                            yield {
                                "type": "stream_chunk",
                                "content": str(stream_event["content"]),
                                "is_final": False,
                                "request_id": request_id,
                            }
                    continue

                if mode == "custom":
                    custom_event = self._build_custom_stream_event(event, request_id=request_id)
                    if custom_event is not None:
                        yield custom_event
                    continue

                if mode != "updates" or not isinstance(event, dict):
                    continue

                for node_name, node_output in event.items():
                    if not isinstance(node_output, dict):
                        continue
                    if "messages" in node_output:
                        node_output = {
                            **node_output,
                            "messages": self._coerce_messages_list(node_output.get("messages")),
                        }
                    last_result.update(node_output)
                    yield {
                        "type": "stream_event",
                        "event_type": "node_update",
                        "content": "",
                        "request_id": request_id,
                        "metadata": {
                            "node_name": node_name,
                            "output_keys": list(node_output.keys()),
                            "contains_messages": "messages" in node_output,
                            "contains_pending_response": "pending_response" in node_output,
                        },
                    }
                    if chunk := node_output.get("pending_response"):
                        new_content = chunk[len(full_response) :]
                        if new_content:
                            full_response = chunk
                            yield {
                                "type": "stream_chunk",
                                "content": new_content,
                                "is_final": False,
                                "request_id": request_id,
                            }

            last_ai_message = self._get_last_ai_message(last_result.get("messages"))
            reasoning_summary = extract_reasoning_summary_text(last_ai_message)
            reasoning_tokens = extract_reasoning_token_count(last_ai_message)
            if reasoning_summary or reasoning_tokens is not None:
                summary_event = build_reasoning_summary_event(
                    reasoning_summary or "",
                    metadata={
                        "reasoning_tokens": reasoning_tokens,
                    },
                )
                yield {
                    "type": "stream_event",
                    "event_type": summary_event["event_type"],
                    "content": summary_event["content"],
                    "request_id": request_id,
                    "metadata": summary_event["metadata"],
                }

            if not full_response:
                fallback_response = self._extract_response(last_result)
                if fallback_response:
                    yield {
                        "type": "stream_chunk",
                        "content": fallback_response,
                        "is_final": True,
                        "request_id": request_id,
                    }
                    return

            yield {
                "type": "stream_chunk",
                "content": "",
                "is_final": True,
                "request_id": request_id,
            }

        except Exception as e:
            logger.error(
                "Error in streaming (request_id=%s): %s",
                request_id,
                e,
                exc_info=True,
            )
            raise

    def _graph_accepts_stream_mode(self) -> bool:
        """Return True when the graph's astream accepts the stream_mode kwarg."""
        astream = getattr(self.graph, "astream", None)
        if not callable(astream):
            return False
        try:
            parameters = inspect.signature(astream).parameters.values()
            return any(
                parameter.name == "stream_mode" or parameter.kind == inspect.Parameter.VAR_KEYWORD
                for parameter in parameters
            )
        except (TypeError, ValueError):
            return False

    def _build_message_stream_events(
        self,
        event: Any,
        *,
        request_id: str,
    ) -> list[dict[str, Any]]:
        """Normalize a LangGraph messages-mode event into stream items."""
        if not isinstance(event, tuple) or len(event) != 2:
            return []

        message_chunk, metadata = event
        if not isinstance(message_chunk, AIMessageChunk):
            return []

        stream_metadata = make_json_safe(metadata)
        events: list[dict[str, Any]] = []
        for reasoning_delta in extract_stream_reasoning_deltas(message_chunk):
            reasoning_event = build_reasoning_delta_event(
                reasoning_delta,
                metadata={
                    "message_chunk_type": type(message_chunk).__name__,
                    "stream_metadata": stream_metadata,
                },
            )
            events.append(
                {
                    "type": "stream_event",
                    "event_type": reasoning_event["event_type"],
                    "content": reasoning_event["content"],
                    "request_id": request_id,
                    "metadata": reasoning_event["metadata"],
                }
            )

        chunk_text = extract_stream_chunk_text(message_chunk)
        events.append(
            {
                "type": "stream_event",
                "event_type": "message_delta",
                "content": chunk_text,
                "request_id": request_id,
                "metadata": {
                    "message_chunk_type": type(message_chunk).__name__,
                    "stream_metadata": stream_metadata,
                    "text_length": len(chunk_text),
                },
            }
        )
        return events

    def _build_custom_stream_event(
        self,
        event: Any,
        *,
        request_id: str,
    ) -> dict[str, Any] | None:
        """Normalize a LangGraph custom-mode payload into an IPC stream item."""

        envelope = normalize_custom_event(event)
        if envelope is None:
            return None
        return {
            "type": "stream_event",
            "event_type": envelope["event_type"],
            "content": envelope["content"],
            "request_id": request_id,
            "metadata": envelope["metadata"],
        }

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
        if self.llm_client:
            await self.llm_client.close()

        self._initialized = False
        logger.info("Progenitor service shutdown complete")

    def _build_graph_input(self, message: str) -> dict[str, Any]:
        """Build the same graph input shape used by ProgenitorLoop."""
        return {"messages": [HumanMessage(content=message)]}

    async def _get_thread_id(self, metadata: dict[str, Any] | None = None) -> str:
        """Use a request-scoped thread_id when provided, otherwise preserve the main thread."""
        requested_thread_id = str((metadata or {}).get("thread_id") or "").strip()
        if requested_thread_id:
            return requested_thread_id
        if self.state_manager:
            current_state = await self.state_manager.get_current_state()
            thread_id = str(current_state.get("thread_id") or "").strip()
            if thread_id:
                return thread_id
        return self._session_id or "progenitor_service"

    def _extract_response(self, result: Any) -> str:
        """Extract the final assistant response from a graph result/state dict."""
        return self._extract_response_details(result)["response"]

    def _extract_response_details(self, result: Any) -> ResponseDetails:
        """Extract response text and reasoning metadata from a graph result/state dict."""
        response_text = ""
        reasoning_text: str | None = None
        reasoning_tokens: int | None = None

        if isinstance(result, dict):
            pending_response = result.get("pending_response")
            if isinstance(pending_response, str) and pending_response.strip():
                response_text = pending_response

            last_ai_message = self._get_last_ai_message(result.get("messages"))
            if last_ai_message is not None:
                extracted = self._extract_message_text(last_ai_message)
                if extracted:
                    response_text = extracted
                reasoning_text = extract_reasoning_summary_text(last_ai_message)
                reasoning_tokens = extract_reasoning_token_count(last_ai_message)

        return {
            "response": response_text or "I processed your request but have no response.",
            "reasoning": reasoning_text or None,
            "reasoning_tokens": reasoning_tokens,
        }

    def _coerce_messages_list(self, messages: Any) -> list[Any]:
        """Coerce LangGraph message containers into a plain list."""
        if messages is None:
            return []
        if isinstance(messages, list):
            return messages
        value = getattr(messages, "value", None)
        if isinstance(value, list):
            return value
        if isinstance(value, tuple):
            return list(value)
        if isinstance(messages, dict):
            return [messages]
        try:
            return list(messages)
        except TypeError:
            return []

    def _get_last_ai_message(self, messages: Any) -> Any | None:
        """Return the most recent assistant message."""
        for msg in reversed(self._coerce_messages_list(messages)):
            msg_type = str(getattr(msg, "type", "")).lower()
            msg_role = str(getattr(msg, "role", "")).lower()
            if msg_type == "ai" or msg_role == "assistant":
                return msg
            if isinstance(msg, dict):
                role = str(msg.get("role", "")).lower()
                msg_type = str(msg.get("type", "")).lower()
                if role == "assistant" or msg_type == "ai":
                    return msg
        return None

    def _extract_message_text(self, msg: Any) -> str | None:
        """Extract assistant-facing text from a message content payload."""
        if msg is None:
            return None

        content = msg.get("content") if isinstance(msg, dict) else getattr(msg, "content", None)
        if isinstance(content, str):
            text = content.strip()
            return text or None

        if isinstance(content, list):
            parts: list[str] = []
            for part in content:
                if isinstance(part, str):
                    text = part.strip()
                    if text:
                        parts.append(text)
                    continue
                if not isinstance(part, dict):
                    continue
                part_type = str(part.get("type", "")).lower()
                if part_type in {"text", "output_text", "message"}:
                    candidate = part.get("text") or part.get("content")
                    if isinstance(candidate, str) and candidate.strip():
                        parts.append(candidate.strip())
            if parts:
                return "\n".join(parts)

        return None

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


async def get_initialized_service(config: ProgenitorConfig | None = None) -> ProgenitorService:
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
