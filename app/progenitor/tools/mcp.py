"""MCP integration for Progenitor."""

import inspect
import logging
import uuid
from contextlib import asynccontextmanager
from typing import Any, cast

import httpx
from a2a.client.client import ClientConfig
from a2a.client.client_factory import ClientFactory
from a2a.types import (
    Message,
    MessageSendConfiguration,
    Part,
    Role,
    TaskIdParams,
    TaskQueryParams,
    TextPart,
)
from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.sessions import Connection, StreamableHttpConnection

from ..config import MCPConfig
from .protocol import BaseSystemTool, ToolContext, ToolResult

logger = logging.getLogger(__name__)


class MCPTool(BaseSystemTool):
    """Expose MCP integration status and discovered tool list."""

    @property
    def name(self) -> str:
        return "mcp"

    @property
    def description(self) -> str:
        return "Show MCP integration status and list discovered MCP tools"

    @property
    def keywords(self) -> list[str]:
        return []

    async def execute(self, context: ToolContext) -> ToolResult:
        llm_client = context.llm_client
        mcp_config = getattr(llm_client, "mcp_config", None)
        mcp_tools = (
            llm_client.get_mcp_tool_registry_entries()
            if llm_client is not None and hasattr(llm_client, "get_mcp_tool_registry_entries")
            else []
        )

        configured_servers = []
        if isinstance(mcp_config, MCPConfig):
            counts_by_server: dict[str, int] = {}
            for tool in mcp_tools:
                server_name = str(tool.get("server_name") or "").strip()
                if server_name:
                    counts_by_server[server_name] = counts_by_server.get(server_name, 0) + 1

            configured_servers = [
                {
                    "name": server.name,
                    "url": server.url,
                    "enabled": server.enabled,
                    "transport": server.transport,
                    "tool_count": counts_by_server.get(server.name, 0),
                }
                for server in mcp_config.servers
            ]

        return ToolResult.ok(
            {
                "enabled": bool(getattr(mcp_config, "enabled_servers", []))
                if mcp_config
                else False,
                "server_count": len(getattr(mcp_config, "enabled_servers", []))
                if isinstance(mcp_config, MCPConfig)
                else 0,
                "tool_count": len(mcp_tools),
                "servers": configured_servers,
                "tools": [
                    {
                        "name": t.get("name"),
                        "description": t.get("description", ""),
                        "server": t.get("server_name"),
                        "original_name": t.get("original_name") or t.get("name"),
                    }
                    for t in mcp_tools
                    if isinstance(t, dict)
                ],
            }
        )


def _build_adapter_connections(mcp_config: MCPConfig) -> dict[str, Connection]:
    connections: dict[str, Connection] = {}
    for server in mcp_config.enabled_servers:
        connection: StreamableHttpConnection = {
            "transport": "streamable_http",
            "url": server.url,
            "headers": dict(server.headers),
        }
        connections[server.name] = connection
    return connections


def _mcp_tool_entry(server_name: str, server_url: str, tool: BaseTool) -> dict[str, Any]:
    tool_name = str(getattr(tool, "name", "") or "").strip()
    prefix = f"{server_name}_"
    original_name = tool_name.removeprefix(prefix) if tool_name.startswith(prefix) else tool_name
    description = str(getattr(tool, "description", "") or "")

    metadata = getattr(tool, "metadata", None)
    if not isinstance(metadata, dict):
        metadata = {}
    tool.metadata = {
        **metadata,
        "mcp_server_name": server_name,
        "mcp_server_url": server_url,
        "mcp_original_name": original_name,
    }

    return {
        "name": tool_name,
        "description": description,
        "type": "mcp_tool",
        "source": server_name,
        "endpoint": server_url,
        "transport": "http",
        "capabilities": [tool_name],
        "server_name": server_name,
        "original_name": original_name,
    }


async def initialize_mcp_langchain_tools(
    mcp_config: MCPConfig,
) -> tuple[list[BaseTool], list[dict[str, Any]]]:
    """Load MCP tools through the official LangChain adapter package."""
    connections = _build_adapter_connections(mcp_config)
    if not connections:
        return [], []

    client = MultiServerMCPClient(connections, tool_name_prefix=True)
    loaded_tools: list[BaseTool] = []
    registry_entries: list[dict[str, Any]] = []
    for server in mcp_config.enabled_servers:
        try:
            server_tools = await client.get_tools(server_name=server.name)
        except Exception as exc:
            logger.warning(
                "Failed to load LangChain MCP tools for server '%s': %s", server.name, exc
            )
            continue
        for tool in server_tools:
            if not str(getattr(tool, "name", "") or "").strip():
                continue
            loaded_tools.append(tool)
            registry_entries.append(_mcp_tool_entry(server.name, server.url, tool))
    logger.info("Loaded %d LangChain MCP tool wrapper(s)", len(loaded_tools))
    return loaded_tools, registry_entries


async def setup_mcp_for_llm_client(
    llm_client,
    mcp_config: MCPConfig,
) -> None:
    """Initialize MCP tools and attach them to the LLM client."""
    llm_client.mcp_config = mcp_config
    close_mcp_context = getattr(llm_client, "close_mcp_context", None)
    if callable(close_mcp_context):
        maybe = close_mcp_context()
        if inspect.isawaitable(maybe):
            await cast(Any, maybe)

    mcp_langchain_tools, registry_entries = await initialize_mcp_langchain_tools(mcp_config)
    llm_client.set_mcp_context(mcp_langchain_tools, registry_entries)


async def refresh_mcp_runtime(
    llm_client,
    tool_registry,
    mcp_config: MCPConfig,
    *,
    reload_config: bool = False,
) -> None:
    """Refresh the live MCP runtime context and sync the in-memory tool registry."""
    effective_config = mcp_config
    if reload_config:
        effective_config = MCPConfig.load(config_path=mcp_config.config_path)

    await setup_mcp_for_llm_client(llm_client, effective_config)
    entries = llm_client.get_mcp_tool_registry_entries()
    if tool_registry is None:
        return

    set_runtime_tools = getattr(tool_registry, "set_runtime_tools", None)
    if callable(set_runtime_tools):
        set_runtime_tools(entries)
        return

    if hasattr(tool_registry, "register_tool"):
        for entry in entries:
            await tool_registry.register_tool(entry)


class A2AClient:
    """Client for calling A2A agents.

    Supports both HTTP (preferred) and stdio transports.
    HTTP agents should expose endpoints at their registered URL.
    """

    def _resolve_agent_url(self, agent_config: dict[str, Any]) -> str:
        url = str(agent_config.get("url") or "").strip()
        if not url:
            raise ValueError("No agent URL configured")
        return url

    def _build_headers(
        self,
        agent_config: dict[str, Any],
        *,
        include_content_type: bool = True,
    ) -> dict[str, str]:
        headers: dict[str, str] = {}
        if include_content_type:
            headers["Content-Type"] = "application/json"
        custom_headers = agent_config.get("headers")
        if isinstance(custom_headers, dict):
            headers.update({str(k): str(v) for k, v in custom_headers.items()})
        return headers

    async def _close_client(self, client: Any) -> None:
        close = getattr(client, "close", None)
        if not callable(close):
            return
        maybe = close()
        if inspect.isawaitable(maybe):
            await cast(Any, maybe)

    @asynccontextmanager
    async def _sdk_client(
        self,
        agent_config: dict[str, Any],
        *,
        timeout: float,
        streaming: bool = False,
        polling: bool = False,
    ):
        url = self._resolve_agent_url(agent_config)
        headers = self._build_headers(agent_config, include_content_type=False)
        async with httpx.AsyncClient(timeout=timeout, headers=headers) as httpx_client:
            client_config = ClientConfig(
                streaming=streaming,
                polling=polling,
                httpx_client=httpx_client,
            )
            client = await cast(Any, ClientFactory).connect(
                url,
                client_config=client_config,
                resolver_http_kwargs={"headers": headers},
            )
            try:
                yield cast(Any, client)
            finally:
                await self._close_client(client)

    @staticmethod
    def _serialize_model(payload: Any) -> Any:
        if hasattr(payload, "model_dump"):
            return payload.model_dump(mode="json", by_alias=True, exclude_none=True)
        return payload

    async def call_agent(
        self,
        agent_config: dict[str, Any],
        request: str,
        context: dict[str, Any] | None = None,
        timeout: float = 60.0,
        blocking: bool = False,
        history_length: int | None = None,
    ) -> dict[str, Any]:
        """Call an A2A agent with a request.

        Args:
            agent_config: Agent configuration from registry.
            request: Request message.
            context: Additional context.
            timeout: Request timeout in seconds.

        Returns:
            Response dict with success/error fields.
        """
        transport = agent_config.get("transport", "http")  # Default to HTTP

        # Check agent status
        status = agent_config.get("status", "stopped")
        if status == "failed":
            return {
                "success": False,
                "error": f"Agent is in failed state: {agent_config.get('last_error', 'Unknown error')}",
            }

        if transport != "http":
            return {
                "success": False,
                "error": f"Unsupported transport: {transport}. Only 'http' is supported.",
            }

        try:
            async with self._sdk_client(
                agent_config,
                timeout=timeout,
                streaming=False,
                polling=not blocking,
            ) as client:
                message = Message(
                    role=cast(Any, getattr(Role, "user", "user")),
                    parts=[Part(root=TextPart(text=request or ""))],
                    message_id=uuid.uuid4().hex,
                    metadata=context or {},
                )
                configuration = MessageSendConfiguration(
                    blocking=blocking,
                    history_length=history_length,
                )
                last_payload: Any = None
                async for item in client.send_message(message, configuration=configuration):
                    if isinstance(item, tuple) and len(item) == 2:
                        task_payload = self._serialize_model(item[0])
                        update_payload = (
                            self._serialize_model(item[1]) if item[1] is not None else None
                        )
                        if isinstance(task_payload, dict) and update_payload is not None:
                            task_payload["latestUpdate"] = update_payload
                        last_payload = task_payload
                    else:
                        last_payload = self._serialize_model(item)
        except (TimeoutError, httpx.TimeoutException):
            return {"success": False, "error": f"Request timeout after {timeout}s"}
        except Exception as e:
            return {"success": False, "error": str(e)}

        if last_payload is None:
            return {"success": False, "error": "Agent returned no response"}
        return {"success": True, "result": last_payload}

    async def get_task(
        self,
        agent_config: dict[str, Any],
        task_id: str,
        history_length: int | None = 20,
        timeout: float = 15.0,
    ) -> dict[str, Any]:
        """Fetch an A2A task snapshot by task ID."""
        try:
            async with self._sdk_client(agent_config, timeout=timeout) as client:
                task = await client.get_task(
                    TaskQueryParams(id=task_id, history_length=history_length)
                )
        except (TimeoutError, httpx.TimeoutException):
            return {"success": False, "error": f"Request timeout after {timeout}s"}
        except Exception as e:
            return {"success": False, "error": str(e)}
        return {"success": True, "result": self._serialize_model(task)}

    async def cancel_task(
        self,
        agent_config: dict[str, Any],
        task_id: str,
        timeout: float = 15.0,
    ) -> dict[str, Any]:
        """Cancel an A2A task by task ID."""
        try:
            async with self._sdk_client(agent_config, timeout=timeout) as client:
                task = await client.cancel_task(TaskIdParams(id=task_id))
        except (TimeoutError, httpx.TimeoutException):
            return {"success": False, "error": f"Request timeout after {timeout}s"}
        except Exception as e:
            return {"success": False, "error": str(e)}
        return {"success": True, "result": self._serialize_model(task)}

    async def get_agent_card(
        self,
        agent_config: dict[str, Any],
        timeout: float = 5.0,
    ) -> dict[str, Any] | None:
        """Fetch the agent card from an HTTP agent.

        Args:
            agent_config: Agent configuration from registry.
            timeout: Request timeout.

        Returns:
            Agent card dict or None if unavailable.
        """
        agent_card_url = agent_config.get("agent_card_url")
        if not agent_card_url:
            url = str(agent_config.get("url") or "").strip()
            if not url:
                return None
            agent_card_url = f"{url}/.well-known/agent-card.json"

        try:
            async with httpx.AsyncClient(
                timeout=timeout,
                headers=self._build_headers(agent_config, include_content_type=False),
            ) as client:
                response = await client.get(str(agent_card_url))
                response.raise_for_status()
                return cast(dict[str, Any], response.json())
        except Exception as e:
            logger.debug("Failed to fetch agent card: %s", e)

        return None
