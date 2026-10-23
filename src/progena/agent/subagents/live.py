"""Expose the live object-agent catalog through DeepAgents background tools."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from deepagents.middleware.async_subagents import (
    AsyncSubAgent,
    AsyncSubAgentMiddleware,
    AsyncSubAgentState,
)
from langchain.agents.middleware.types import AgentMiddleware

from src.progena.agent.object_agents.registry import ObjectAgentRegistry


class LiveAsyncSubagentMiddleware(AgentMiddleware):
    """Use SDK tools with refreshed descriptors, retaining the parent graph/state."""

    state_schema = AsyncSubAgentState

    def __init__(
        self,
        registry: ObjectAgentRegistry,
        *,
        base_url: str,
        static_subagents: list[AsyncSubAgent] | None = None,
    ) -> None:
        self._registry = registry
        self._base_url = base_url.rstrip("/")
        self._static = static_subagents or []
        self._known: dict[str, AsyncSubAgent] = {}
        self.tools = self._snapshot().tools

    def descriptors(self) -> list[AsyncSubAgent]:
        specs: dict[str, AsyncSubAgent] = {spec["name"]: spec for spec in self._static}
        for spec in self._registry.descriptors:
            specs[spec["name"]] = AsyncSubAgent(
                name=spec["name"],
                description=spec["description"],
                graph_id=spec["name"],
                url=f"{self._base_url}/agent-protocol",
            )
        self._known.update(specs)
        return list(specs.values())

    def _snapshot(self) -> AsyncSubAgentMiddleware:
        return AsyncSubAgentMiddleware(async_subagents=self.descriptors())

    def _model_request(self, request: Any, snapshot: AsyncSubAgentMiddleware) -> Any:
        names = {tool.name for tool in snapshot.tools}
        return request.override(
            tools=[tool for tool in request.tools if getattr(tool, "name", None) not in names]
            + list(snapshot.tools)
        )

    def wrap_model_call(self, request: Any, handler: Callable[..., Any]) -> Any:
        snapshot = self._snapshot()
        return snapshot.wrap_model_call(self._model_request(request, snapshot), handler)

    async def awrap_model_call(self, request: Any, handler: Callable[..., Any]) -> Any:
        snapshot = self._snapshot()
        return await snapshot.awrap_model_call(self._model_request(request, snapshot), handler)

    def _bind(self, request: Any) -> Any:
        name = request.tool_call["name"]
        if name not in {tool.name for tool in self.tools}:
            return request
        specs = {spec["name"]: spec for spec in self.descriptors()}
        if name != "start_async_task":
            # Existing tasks remain checkable after deleting their definition.
            specs = {**self._known, **specs}
            for task in (request.state.get("async_tasks") or {}).values():
                agent_name = task.get("agent_name")
                if agent_name and agent_name not in specs:
                    specs[agent_name] = AsyncSubAgent(
                        name=agent_name,
                        description="Previously invoked object agent.",
                        graph_id=agent_name,
                        url=f"{self._base_url}/agent-protocol",
                    )
        snapshot = AsyncSubAgentMiddleware(async_subagents=list(specs.values()))
        tool = next(
            (tool for tool in snapshot.tools if tool.name == name),
            None,
        )
        return request.override(tool=tool) if tool is not None else request

    def wrap_tool_call(self, request: Any, handler: Callable[..., Any]) -> Any:
        return handler(self._bind(request))

    async def awrap_tool_call(self, request: Any, handler: Callable[..., Any]) -> Any:
        return await handler(self._bind(request))
