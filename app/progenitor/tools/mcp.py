"""
MCP Client for Progenitor.

Handles communication with MCP servers via stdio or HTTP transports.
"""

import asyncio
import inspect
import json
import logging
import shlex
import uuid
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
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

from ..config import MCPConfig, MCPServerConfig
from ..mcp_tool_utils import (
    get_mcp_tool_description,
    get_mcp_tool_name,
    get_mcp_tool_parameters,
    parse_mcp_jsonrpc_response,
)
from .protocol import BaseSystemTool, ToolContext, ToolResult
from .registry import normalize_tool_name

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
        mcp_tools = getattr(llm_client, "mcp_tools", None) or []
        runtime_servers = getattr(llm_client, "mcp_servers", None) or {}

        configured_servers = []
        if isinstance(mcp_config, MCPConfig):
            configured_servers = [
                {
                    "name": server.name,
                    "url": server.url,
                    "enabled": server.enabled,
                    "transport": server.transport,
                    "tool_count": len(runtime_servers.get(server.name, {}).get("tools", []))
                    if isinstance(runtime_servers, dict)
                    else 0,
                }
                for server in mcp_config.servers
            ]

        return ToolResult.ok(
            {
                "enabled": bool(getattr(mcp_config, "enabled_servers", []))
                if mcp_config
                else False,
                "server_count": len(runtime_servers) if isinstance(runtime_servers, dict) else 0,
                "tool_count": len(mcp_tools),
                "servers": configured_servers,
                "tools": [
                    {
                        "name": t.get("name"),
                        "description": get_mcp_tool_description(t),
                        "server": t.get("mcp_server_name"),
                        "original_name": t.get("mcp_original_name") or t.get("name"),
                    }
                    for t in mcp_tools
                    if isinstance(t, dict)
                ],
            }
        )


# Allowlist of safe command prefixes for MCP servers
# Commands must start with one of these to be executed
ALLOWED_COMMAND_PREFIXES = (
    "python",
    "node",
    "npx",
    "uvx",
    "deno",
)


async def initialize_mcp_server(
    server_config: MCPServerConfig,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Initialize one remote HTTP MCP server and return its discovered tools."""
    if not server_config.enabled:
        return None, []

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    headers.update(server_config.headers)
    mcp_client = httpx.AsyncClient(
        timeout=httpx.Timeout(30.0),
        headers=headers,
    )

    try:
        logger.info("Fetching MCP tools from %s (%s)", server_config.url, server_config.name)

        init_request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "progenitor", "version": "0.0.0"},
            },
        }
        init_response = await mcp_client.post(server_config.url, json=init_request)
        init_response.raise_for_status()

        session_id = init_response.headers.get("mcp-session-id")
        if session_id:
            mcp_client.headers["mcp-session-id"] = session_id

        list_request = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
        }
        response = await mcp_client.post(server_config.url, json=list_request)
        response.raise_for_status()

        parsed = parse_mcp_jsonrpc_response(response.text) or {}
        result = parsed.get("result", parsed) if isinstance(parsed, dict) else {}
        tools = result.get("tools", []) if isinstance(result, dict) else []
        tool_list = cast(list[dict[str, Any]], tools if isinstance(tools, list) else [])

        return (
            {
                "name": server_config.name,
                "url": server_config.url,
                "description": server_config.description,
                "headers": dict(server_config.headers),
                "client": mcp_client,
            },
            tool_list,
        )
    except (httpx.HTTPError, ValueError, json.JSONDecodeError) as exc:
        logger.warning("Failed to initialize MCP server '%s': %s", server_config.name, exc)
        await mcp_client.aclose()
        return None, []


async def initialize_mcp_tools(
    mcp_config: MCPConfig,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """Initialize all configured remote HTTP MCP servers and flatten their tools."""
    discovered_servers: list[tuple[MCPServerConfig, dict[str, Any], list[dict[str, Any]]]] = []
    for server_config in mcp_config.enabled_servers:
        runtime_server, tools = await initialize_mcp_server(server_config)
        if runtime_server is None:
            continue
        discovered_servers.append((server_config, runtime_server, tools))

    name_counts: dict[str, int] = {}
    for _server_config, _runtime_server, tools in discovered_servers:
        for tool in tools:
            original_name = get_mcp_tool_name(tool)
            if original_name is None or get_mcp_tool_parameters(tool, require_object=True) is None:
                continue
            name_counts[original_name] = name_counts.get(original_name, 0) + 1

    runtime_servers: dict[str, dict[str, Any]] = {}
    flattened_tools: list[dict[str, Any]] = []
    used_aliases: set[str] = set()

    for server_config, runtime_server, tools in discovered_servers:
        server_tools: list[dict[str, Any]] = []
        for tool in tools:
            original_name = get_mcp_tool_name(tool)
            if original_name is None:
                continue
            if get_mcp_tool_parameters(tool, require_object=True) is None:
                logger.warning(
                    "Skipping MCP tool '%s' from server '%s' because it is missing a standard object inputSchema",
                    original_name,
                    server_config.name,
                )
                continue

            alias = original_name
            if name_counts.get(original_name, 0) > 1:
                alias = normalize_tool_name(f"{server_config.name}_{original_name}")

            suffix = 2
            unique_alias = alias
            while unique_alias in used_aliases:
                unique_alias = normalize_tool_name(f"{alias}_{suffix}")
                suffix += 1

            used_aliases.add(unique_alias)
            aliased_tool = dict(tool)
            aliased_tool["name"] = unique_alias
            aliased_tool["mcp_original_name"] = original_name
            aliased_tool["mcp_server_name"] = server_config.name
            aliased_tool["mcp_server_url"] = server_config.url
            if unique_alias != original_name:
                description = get_mcp_tool_description(aliased_tool)
                if description:
                    aliased_tool["description"] = (
                        f"{description} [server: {server_config.name}, original: {original_name}]"
                    )
            server_tools.append(aliased_tool)
            flattened_tools.append(aliased_tool)

        runtime_servers[server_config.name] = {
            **runtime_server,
            "tools": server_tools,
        }

    logger.info(
        "Initialized %d MCP server(s) with %d tool(s)",
        len(runtime_servers),
        len(flattened_tools),
    )
    return runtime_servers, flattened_tools


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


def _build_mcp_tool_alias_lookup(
    mcp_tools: list[dict[str, Any]],
) -> dict[tuple[str, str], dict[str, Any]]:
    aliases: dict[tuple[str, str], dict[str, Any]] = {}
    for tool in mcp_tools:
        server_name = str(tool.get("mcp_server_name") or "").strip()
        original_name = str(tool.get("mcp_original_name") or tool.get("name") or "").strip()
        if not server_name or not original_name:
            continue
        aliases[(server_name, original_name)] = tool
    return aliases


async def initialize_mcp_langchain_tools(
    mcp_config: MCPConfig,
    mcp_tools: list[dict[str, Any]],
) -> list[BaseTool]:
    """Load LangChain MCP tools using the official adapter package."""
    connections = _build_adapter_connections(mcp_config)
    if not connections:
        return []

    client = MultiServerMCPClient(connections)
    alias_lookup = _build_mcp_tool_alias_lookup(mcp_tools)

    async def _load_server_tools(server_name: str) -> list[BaseTool]:
        try:
            loaded_tools = await client.get_tools(server_name=server_name)
        except Exception as exc:
            logger.warning(
                "Failed to load LangChain MCP tools for server '%s': %s", server_name, exc
            )
            return []

        aliased_tools: list[BaseTool] = []
        for tool in loaded_tools:
            original_name = str(getattr(tool, "name", "") or "").strip()
            if not original_name:
                continue

            alias_tool = alias_lookup.get((server_name, original_name))
            if alias_tool is not None:
                alias_name = str(alias_tool.get("name") or original_name).strip()
                if alias_name:
                    tool.name = alias_name
                alias_description = get_mcp_tool_description(alias_tool)
                if alias_description:
                    tool.description = alias_description
            aliased_tools.append(tool)
        return aliased_tools

    loaded_tool_groups = await asyncio.gather(
        *[_load_server_tools(server.name) for server in mcp_config.enabled_servers]
    )
    flattened_tools = [tool for tools in loaded_tool_groups for tool in tools]
    logger.info("Loaded %d LangChain MCP tool wrapper(s)", len(flattened_tools))
    return flattened_tools


async def setup_mcp_for_llm_client(
    llm_client,
    mcp_config: MCPConfig,
) -> None:
    """Initialize MCP tools and attach them to the LLM client."""
    llm_client.mcp_config = mcp_config
    close_mcp_context = getattr(llm_client, "close_mcp_context", None)
    if callable(close_mcp_context):
        close_mcp_context_typed = cast(Callable[[], Awaitable[None]], close_mcp_context)
        await close_mcp_context_typed()

    mcp_servers, mcp_tools = await initialize_mcp_tools(mcp_config)
    mcp_langchain_tools = await initialize_mcp_langchain_tools(mcp_config, mcp_tools)
    llm_client.set_mcp_context(mcp_servers, mcp_tools, mcp_langchain_tools)


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


def _validate_command(command: str | list[str]) -> list[str]:
    """
    Safely parse and validate a command for execution.

    Args:
        command: Command string or list of command parts

    Returns:
        List of command parts, safely parsed

    Raises:
        ValueError: If command is invalid or not in allowlist
    """
    if isinstance(command, list):
        cmd_parts = command
    else:
        # Use shlex.split for proper shell escaping (prevents injection)
        try:
            cmd_parts = shlex.split(command)
        except ValueError as e:
            raise ValueError(f"Invalid command syntax: {e}") from e

    if not cmd_parts:
        raise ValueError("Empty command")

    # Validate command against allowlist
    executable = cmd_parts[0].lower()
    # Extract just the executable name (handle paths like /usr/bin/python)
    executable_name = Path(executable).name.lower()
    # Remove common extensions
    if executable_name.endswith(".exe"):
        executable_name = executable_name[:-4]

    if not any(executable_name.startswith(prefix) for prefix in ALLOWED_COMMAND_PREFIXES):
        logger.warning(
            "Command '%s' not in allowlist. Allowed prefixes: %s",
            executable_name,
            ALLOWED_COMMAND_PREFIXES,
        )
        raise ValueError(
            f"Command '{executable_name}' not allowed. "
            f"Allowed: {', '.join(ALLOWED_COMMAND_PREFIXES)}"
        )

    return cmd_parts


class MCPClient:
    """Client for calling MCP servers/tools."""

    def __init__(self, project_root: Path | None = None):
        self._processes: dict[str, asyncio.subprocess.Process] = {}
        self.project_root = Path(project_root) if project_root else Path.cwd()

    async def call_tool(
        self,
        tool_config: dict[str, Any],
        method: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Call an MCP tool method."""
        transport = tool_config.get("transport", "stdio")

        if transport == "stdio":
            return await self._call_stdio(tool_config, method, params)
        elif transport == "http":
            return await self._call_http(tool_config, method, params)
        else:
            raise ValueError(f"Unknown transport: {transport}")

    async def _call_stdio(
        self,
        tool_config: dict[str, Any],
        method: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Call an MCP server via stdio."""
        command = tool_config.get("command")
        if not command:
            raise ValueError("No command specified for stdio transport")

        # Build the JSON-RPC request
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params or {},
        }

        # Safely parse and validate command (prevents command injection)
        cmd_parts = _validate_command(command)

        # Start MCP server process directly (no container isolation)
        process = await asyncio.create_subprocess_exec(
            *cmd_parts,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        # Send request and get response
        request_bytes = json.dumps(request).encode() + b"\n"
        stdout, stderr = await asyncio.wait_for(
            process.communicate(input=request_bytes),
            timeout=float(tool_config.get("timeout", 30)),
        )

        if process.returncode != 0:
            return {
                "error": f"MCP server exited with code {process.returncode}",
                "stderr": stderr.decode() if stderr else None,
            }

        # Parse response
        try:
            response = json.loads(stdout.decode())
            return cast(dict[str, Any], response.get("result", response))
        except json.JSONDecodeError as e:
            return {
                "error": f"Invalid JSON response: {e}",
                "stdout": stdout.decode() if stdout else None,
            }

    async def _call_http(
        self,
        tool_config: dict[str, Any],
        method: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Call an MCP server via HTTP."""
        import aiohttp

        endpoint = tool_config.get("endpoint")
        if not endpoint:
            raise ValueError("No URL specified for HTTP transport")

        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params or {},
        }

        headers = {"Content-Type": "application/json"}
        headers.update(tool_config.get("headers", {}))

        try:
            async with (
                aiohttp.ClientSession() as session,
                session.post(
                    endpoint,
                    json=request,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as response,
            ):
                parsed = parse_mcp_jsonrpc_response(await response.text())
                if not isinstance(parsed, dict):
                    raise ValueError("Invalid MCP JSON-RPC response")
                return cast(dict[str, Any], parsed.get("result", parsed))
        except Exception as e:
            return {"error": str(e)}

    async def list_tools(self, tool_config: dict[str, Any]) -> list[dict[str, Any]]:
        """List available tools from an MCP server."""
        result = await self.call_tool(tool_config, "tools/list")
        return cast(
            list[dict[str, Any]], result.get("tools", []) if isinstance(result, dict) else []
        )

    async def healthcheck(self, tool_config: dict[str, Any]) -> bool:
        """Check if an MCP server is healthy."""
        try:
            result = await self.call_tool(tool_config, "ping")
            return not isinstance(result, dict) or "error" not in result
        except (TimeoutError, OSError, ValueError) as e:
            logger.debug("MCP healthcheck failed: %s", e)
            return False


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
