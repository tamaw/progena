"""
Register Node - Updates registry after successful build.

Tests newly built agents, registers them in the local tool registry,
and registers them with the LiteLLM proxy so Progenitor can invoke
them via LiteLLM endpoints rather than directly.
"""

import logging
import os
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

from ..runtime.agent_runner import AgentRunner
from ..runtime.time_utils import utcnow_isoformat
from ..state.models import AgentRegistryEntry, ProgenitorState

if TYPE_CHECKING:
    from ..config import ProgenitorConfig

logger = logging.getLogger(__name__)

LITELLM_BASE_URL = os.getenv("LITELLM_BASE_URL", "http://localhost:4000")
LITELLM_API_KEY = os.getenv("LITELLM_API_KEY", "")


def _litellm_headers() -> dict[str, str]:
    key = LITELLM_API_KEY.strip()
    headers: dict[str, str] = {}
    if key:
        headers["Authorization"] = f"Bearer {key}"
        headers["x-litellm-api-key"] = key
    return headers


def _litellm_agents_url() -> str:
    base = LITELLM_BASE_URL.rstrip("/")
    if base.endswith("/v1"):
        return f"{base}/agents"
    return f"{base}/v1/agents"


def _derive_agent_card_url(api_base: str) -> str:
    base = api_base.rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    return f"{base}/.well-known/agent.json"


async def _register_with_litellm(
    model_name: str,
    api_base: str,
    description: str,
    agent_card_url: str | None = None,
) -> bool:
    """Register an agent or MCP server with the LiteLLM proxy.

    Agent builds are registered via /v1/agents (A2A).
    MCP builds are registered via /model/new (OpenAI model routing).
    """
    headers = _litellm_headers()
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            if model_name.startswith("agent/"):
                agent_name = model_name.split("/", 1)[1]
                card_url = agent_card_url or _derive_agent_card_url(api_base)
                card_resp = await client.get(card_url)
                if not card_resp.is_success:
                    logger.warning(
                        "REGISTER: Failed to fetch agent card for %s: %s %s",
                        model_name, card_resp.status_code, card_resp.text[:300],
                    )
                    return False

                agent_payload = {
                    "agent_name": agent_name,
                    "agent_card_params": card_resp.json(),
                    "litellm_params": {"make_public": True},
                }
                agents_url = _litellm_agents_url()
                resp = await client.post(agents_url, json=agent_payload, headers=headers)
                if resp.is_success:
                    logger.info("REGISTER: Registered %s with LiteLLM agents API", model_name)
                    return True

                list_resp = await client.get(agents_url, headers=headers)
                if list_resp.is_success:
                    agents = list_resp.json() if isinstance(list_resp.json(), list) else []
                    agent_id = next(
                        (
                            str(a.get("agent_id"))
                            for a in agents
                            if isinstance(a, dict) and str(a.get("agent_name") or "") == agent_name and a.get("agent_id")
                        ),
                        "",
                    )
                    if agent_id:
                        update_resp = await client.patch(
                            f"{agents_url}/{agent_id}",
                            json=agent_payload,
                            headers=headers,
                        )
                        if update_resp.is_success:
                            logger.info("REGISTER: Updated %s via LiteLLM agents API", model_name)
                            return True

                logger.warning(
                    "REGISTER: LiteLLM agent registration failed for %s: %s %s",
                    model_name, resp.status_code, resp.text[:300],
                )
                return False

            model_payload = {
                "model_name": model_name,
                "litellm_params": {
                    "model": f"openai/{model_name}",
                    "api_base": api_base,
                    "api_key": LITELLM_API_KEY,
                },
                "model_info": {
                    "description": description,
                    "id": model_name,
                },
            }
            resp = await client.post(
                f"{LITELLM_BASE_URL.rstrip('/')}/model/new",
                json=model_payload,
                headers=headers,
            )
            if resp.is_success:
                logger.info("REGISTER: Registered %s with LiteLLM", model_name)
                return True
            logger.warning(
                "REGISTER: LiteLLM model registration failed for %s: %s %s",
                model_name, resp.status_code, resp.text[:300],
            )
    except Exception as exc:
        logger.warning("REGISTER: LiteLLM call failed for %s: %s", model_name, exc)
    return False


def create_node(
    tool_registry,
    state_manager,
    config: "ProgenitorConfig | None" = None,
) -> Callable[[ProgenitorState], Coroutine[Any, Any, ProgenitorState]]:
    """Create the register node function.

    Args:
        tool_registry: Registry for agents and tools.
        state_manager: State manager for logging decisions.
        config: ProgenitorConfig for project root path.
    """
    if config and hasattr(config, 'storage') and config.storage.worktree_dir:
        project_root = config.storage.worktree_dir.parent
    else:
        project_root = Path.cwd()

    agent_runner = AgentRunner(
        project_root=project_root,
        log_callback=state_manager.log_decision if state_manager else None,
    )

    async def register_node(state: ProgenitorState) -> ProgenitorState:
        """Register new/updated agents and tools, then register with LiteLLM."""
        current_item = state.get("current_work_item")
        validation_result = state.get("validation_result_v2") or {}

        if not current_item:
            return {}

        # Only register if validation passed
        if validation_result.get("has_blocking_issues", True):
            return {}

        # Get item details
        if isinstance(current_item, dict):
            item_type = current_item.get("item_type", "")
            result_data = current_item.get("result", {})
        else:
            item_type = current_item.item_type
            result_data = current_item.result or {}

        spec = result_data.get("spec", {})

        try:
            if item_type == "build_agent":
                await _register_agent(spec, tool_registry, agent_runner, state_manager)
            elif item_type == "build_mcp":
                await _register_tool(spec, tool_registry, state_manager)
            elif item_type == "build_progenitor":
                # Core changes do not register new capabilities.
                pass
            elif item_type == "upgrade":
                await _register_upgrade(spec, tool_registry)
        except Exception as exc:
            logger.error("REGISTER: Failed to register %s: %s", item_type, exc, exc_info=True)
            return {}

        agents = tool_registry.get_agents()
        return {"agents": agents}

    async def _register_agent(spec, tool_registry, agent_runner, state_manager):
        """Register a newly built agent."""
        agent_name = spec.get("name", "unnamed_agent")
        entrypoint = f"app/agents/{agent_name}/agent.py"
        port = tool_registry.allocate_port()

        entry = AgentRegistryEntry(
            name=agent_name,
            kind="a2a_agent",
            description=spec.get("description", ""),
            capabilities=spec.get("capabilities", []),
            entrypoint=entrypoint,
            transport="http",
            port=port,
            endpoint=f"http://localhost:{port}",
            agent_card_url=f"http://localhost:{port}/.well-known/agent.json",
        )

        await tool_registry.register_agent(entry)

        # Test the newly built agent
        logger.info("REGISTER: Testing agent '%s'...", agent_name)
        test_result = await agent_runner.test_agent(
            agent_name=agent_name,
            entrypoint=entrypoint,
        )

        await state_manager.log_decision({
            "event": "agent_tested",
            "agent_name": agent_name,
            "test_passed": test_result.success,
            "test_response": str(test_result.response)[:500] if test_result.response else None,
            "test_error": test_result.error,
            "duration_ms": test_result.duration_ms,
            "timestamp": utcnow_isoformat(),
        })

        # Register with LiteLLM so Progenitor uses it via proxy
        await _register_with_litellm(
            model_name=f"agent/{agent_name}",
            api_base=f"http://localhost:{port}/v1",
            description=spec.get("description", agent_name),
            agent_card_url=entry.agent_card_url,
        )

        await state_manager.log_decision({
            "event": "agent_registered",
            "agent_name": entry.name,
            "capabilities": entry.capabilities,
            "timestamp": utcnow_isoformat(),
        })

    async def _register_tool(spec, tool_registry, state_manager):
        """Register a newly built MCP tool."""
        tool_name = spec.get("name", "unnamed_tool")
        entrypoint = f"app/mcp/{tool_name}/server.py"

        tool_entry = {
            "name": tool_name,
            "kind": "mcp_server",
            "description": spec.get("description", ""),
            "capabilities": spec.get("capabilities", []),
            "command": f"python {entrypoint}",
            "transport": "stdio",
            "created_at": utcnow_isoformat(),
        }

        await tool_registry.register_tool(tool_entry)

        # Register MCP with LiteLLM as well
        await _register_with_litellm(
            model_name=f"mcp/{tool_name}",
            api_base=f"{LITELLM_BASE_URL}/mcp",
            description=spec.get("description", tool_name),
        )

        await state_manager.log_decision({
            "event": "tool_registered",
            "tool_name": tool_name,
            "capabilities": tool_entry["capabilities"],
            "timestamp": utcnow_isoformat(),
        })

    async def _register_upgrade(spec, tool_registry):
        """Upgrade capabilities of an existing agent/tool."""
        target_name = spec.get("name")
        if not target_name:
            return

        existing = tool_registry.find_by_name(target_name)
        if not existing:
            logger.warning("REGISTER: Upgrade target %r not found in registry", target_name)
            return

        new_caps = list(set(
            existing.get("capabilities", []) +
            spec.get("capabilities", [])
        ))
        existing["capabilities"] = new_caps
        existing["updated_at"] = utcnow_isoformat()

        if existing.get("kind") == "a2a_agent":
            entry = AgentRegistryEntry(**existing)
            await tool_registry.register_agent(entry)
        else:
            await tool_registry.register_tool(existing)

    return register_node
