"""Live object delegation and task discovery without replacing the parent graph."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable
from pathlib import Path
from typing import Any

from deepagents.middleware.subagents import CompiledSubAgent, SubAgentMiddleware
from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ToolCallRequest
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool

from .object_agent_manifest import ObjectAgentSource, discover_object_agents

logger = logging.getLogger(__name__)


class ObjectAgentRegistry:
    """Publish a complete discovered generation into the shared routine map.

    Bundled agents are read-only files; other definitions are Postgres-backed.
    Publication has no await points; callers already holding a runnable retain
    it, while subsequent delegation sees creates, deletes, and renames.
    """

    def __init__(
        self,
        *,
        state_manager: Any,
        proxy_factory: Callable[[ObjectAgentSource], CompiledSubAgent],
        reserved_names: Iterable[str] = (),
        native_root: Path | None = None,
    ) -> None:
        self._state_manager = state_manager
        self._native_root = native_root
        self._proxy_factory = proxy_factory
        self._reserved_names = frozenset({"general-purpose", *reserved_names})
        self._lock = asyncio.Lock()
        self.callables: dict[str, Any] = {}
        self.descriptors: tuple[CompiledSubAgent, ...] = ()

    async def refresh(self) -> None:
        async with self._lock:
            sources = await discover_object_agents(self._state_manager, self._native_root)
            specs: list[CompiledSubAgent] = []
            for source in sources:
                if source.name in self._reserved_names:
                    logger.warning("Skipping object agent %r: reserved name", source.name)
                    continue
                specs.append(self._proxy_factory(source))
            # Build everything first, preserving the prior generation on failure.
            callables = {spec["name"]: spec["runnable"] for spec in specs}
            self.callables.clear()
            self.callables.update(callables)
            self.descriptors = tuple(specs)


class LiveSubagentMiddleware(AgentMiddleware):
    """Bind SDK task tools from immutable snapshots through public hooks.

    Only synchronous task descriptors belong here. The SDK's independent
    AsyncSubAgentMiddleware continues to own the async delegation tools.
    No compiled tool closures or parent graph/checkpointer are mutated.
    """

    def __init__(
        self,
        registry: ObjectAgentRegistry,
        *,
        static_subagents: Iterable[CompiledSubAgent] = (),
        backend: Any,
        state_schema: type | None = None,
        private_state_keys: frozenset[str] = frozenset(),
    ) -> None:
        self._registry = registry
        self._static = tuple(static_subagents)
        self._backend = backend
        self._state_schema = state_schema
        self._private_state_keys = private_state_keys

    def _snapshot(self) -> SubAgentMiddleware | None:
        objects = self._registry.descriptors
        names = {spec["name"] for spec in objects}
        specs = [*objects, *(spec for spec in self._static if spec["name"] not in names)]
        if not specs:
            return None
        return SubAgentMiddleware(
            backend=self._backend,
            subagents=specs,
            state_schema=self._state_schema,
            private_state_keys=self._private_state_keys,
        )

    def _model_request(self, request: ModelRequest[Any], snapshot: SubAgentMiddleware) -> Any:
        original = next(
            (tool for tool in request.tools if isinstance(tool, BaseTool) and tool.name == "task"),
            None,
        )
        task_tool = snapshot.tools[0]
        if original is not None:
            # Retain the SDK's built-in general-purpose description. Copy the
            # tool rather than mutating either generation's shared instance.
            specs = (*self._static, *self._registry.descriptors)
            descriptions = {spec["name"]: spec["description"] for spec in specs}
            live_description = "\n".join(
                f"- {name}: {description}" for name, description in descriptions.items()
            )
            task_tool = task_tool.model_copy(
                update={
                    "description": original.description
                    + "\n\nLive task agents:\n"
                    + live_description
                }
            )
        tools = [tool for tool in request.tools if getattr(tool, "name", None) != "task"]
        return request.override(tools=[*tools, task_tool])

    def wrap_model_call(self, request: ModelRequest[Any], handler: Callable[..., Any]) -> Any:
        snapshot = self._snapshot()
        if snapshot is None:
            return handler(request)
        return snapshot.wrap_model_call(self._model_request(request, snapshot), handler)

    async def awrap_model_call(
        self, request: ModelRequest[Any], handler: Callable[..., Awaitable[Any]]
    ) -> Any:
        snapshot = self._snapshot()
        if snapshot is None:
            return await handler(request)
        return await snapshot.awrap_model_call(self._model_request(request, snapshot), handler)

    def _bind_task(self, request: ToolCallRequest) -> ToolCallRequest | ToolMessage:
        if request.tool_call["name"] != "task":
            return request
        # The built-in general-purpose agent remains owned by DeepAgents.
        if request.tool_call["args"].get("subagent_type") == "general-purpose" and request.tool:
            return request
        snapshot = self._snapshot()
        if snapshot is None:
            return ToolMessage(
                content="No synchronous subagents are currently available.",
                tool_call_id=request.tool_call["id"],
                status="error",
            )
        return request.override(tool=snapshot.tools[0])

    def wrap_tool_call(self, request: ToolCallRequest, handler: Callable[..., Any]) -> Any:
        bound = self._bind_task(request)
        return bound if isinstance(bound, ToolMessage) else handler(bound)

    async def awrap_tool_call(
        self, request: ToolCallRequest, handler: Callable[..., Awaitable[Any]]
    ) -> Any:
        bound = self._bind_task(request)
        return bound if isinstance(bound, ToolMessage) else await handler(bound)
