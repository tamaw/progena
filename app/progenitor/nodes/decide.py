"""
Decide Node - Routes user input to appropriate action path.
"""

import logging
from datetime import datetime
from typing import Callable, Coroutine, Any, Optional

import re

from openai import APIConnectionError, RateLimitError

from ..state.models import ProgenitorState, Decision, DecisionAction, WorkItem
from ..state.helpers import is_work_item_actionable
from ..runtime.time_utils import utcnow_isoformat
from .errors import RecoverableError, LLMError, ValidationError

logger = logging.getLogger(__name__)


def _looks_like_agent_build_request(user_input: str) -> bool:
    """Heuristic: detect explicit requests to build/create a new agent."""
    text = (user_input or "").lower()
    if not text.strip():
        return False

    has_build_intent = any(
        phrase in text
        for phrase in (
            "build ",
            "create ",
            "scaffold ",
            "generate ",
            "make ",
            "implement ",
        )
    )
    has_agent_intent = any(
        phrase in text
        for phrase in (
            " a2a agent",
            " an a2a agent",
            " agent",
            " assistant agent",
        )
    )

    # Reduce false positives like "build a plan" or "build a docker image".
    has_non_agent_build = any(
        phrase in text
        for phrase in (
            "build a plan",
            "build the plan",
            "build a docker",
            "build an image",
            "build the image",
            "build a container",
            "build the container",
        )
    )

    return has_build_intent and has_agent_intent and not has_non_agent_build


def _looks_like_agent_discovery_request(user_input: str) -> bool:
    """Heuristic: detect requests to rediscover/rewire agents."""
    text = (user_input or "").lower()
    if not text.strip():
        return False

    has_discovery_intent = any(
        phrase in text
        for phrase in (
            "rewire",
            "rediscover",
            "rescan",
            "refresh",
            "reload",
            "re-scan",
            "re-discover",
            "scan agents",
            "discover agents",
            "update registry",
            "refresh registry",
            "sync agents",
        )
    )

    has_agent_context = any(
        phrase in text
        for phrase in (
            "agent",
            "registry",
        )
    )

    return has_discovery_intent or (has_agent_context and "refresh" in text)


def _looks_like_list_tools_request(user_input: str) -> bool:
    """Heuristic: detect requests to list available tools/capabilities."""
    text = (user_input or "").lower()
    if not text.strip():
        return False

    has_list_intent = any(
        phrase in text
        for phrase in (
            "list tool",
            "list all tool",
            "show tool",
            "show all tool",
            "what tool",
            "available tool",
            "list capabilit",
            "show capabilit",
            "what can you do",
            "your capabilit",
            "all available",
        )
    )

    has_tool_context = any(
        phrase in text
        for phrase in (
            "tool",
            "capabilit",
            "function",
        )
    )

    # Direct patterns
    direct_patterns = [
        "list tools",
        "show tools",
        "list your tools",
        "show your tools",
        "what tools",
        "which tools",
        "available tools",
    ]

    return has_list_intent or any(pattern in text for pattern in direct_patterns)


def _looks_like_schedule_request(user_input: str) -> bool:
    """Heuristic: detect requests to schedule/remind/do something at a time."""
    text = (user_input or "").lower()
    if not text.strip():
        return False

    # Time indicators
    has_time_indicator = any(
        phrase in text
        for phrase in (
            " at ",
            "every day",
            "every morning",
            "every evening",
            "every hour",
            "every minute",
            "in the morning",
            "in the evening",
            "tomorrow",
            "next week",
            "in 5 minutes",
            "in 10 minutes",
            "in 15 minutes",
            "in 30 minutes",
            "in an hour",
            "in 1 hour",
            "in 2 hours",
            "daily",
            "weekly",
        )
    )

    # Schedule/reminder intent
    has_schedule_intent = any(
        phrase in text
        for phrase in (
            "remind",
            "schedule",
            "set a reminder",
            "notify me",
            "tell me at",
            "say to me at",
            "alert me",
            "wake me",
        )
    )

    # Time patterns like "9am", "10:30", "3pm"
    has_time_pattern = bool(re.search(r'\d{1,2}(?::\d{2})?\s*(?:am|pm)', text))

    return (has_time_indicator and has_time_pattern) or has_schedule_intent


def _extract_schedule_params(user_input: str) -> dict:
    """Extract schedule parameters from user input."""
    text = user_input.lower()

    # Try to extract time expression
    time_patterns = [
        r'(every day at \d{1,2}(?::\d{2})?\s*(?:am|pm)?)',
        r'(at \d{1,2}(?::\d{2})?\s*(?:am|pm)?)',
        r'(in \d+ (?:minute|hour|day)s?)',
        r'(tomorrow(?: at \d{1,2}(?::\d{2})?\s*(?:am|pm)?)?)',
        r'(every (?:morning|evening|hour|day|week))',
    ]

    time_expression = ""
    for pattern in time_patterns:
        match = re.search(pattern, text)
        if match:
            time_expression = match.group(1)
            break

    return {
        "user_request": user_input,
        "time_expression": time_expression,
    }


def _infer_target_capabilities(user_input: str) -> list[str]:
    """Infer a likely capability name from the user's request."""
    text = (user_input or "").lower()

    if "hello world" in text or "hello-world" in text:
        return ["hello_world_a2a_agent"]

    # Try to extract an agent name: "build/create <name> agent"
    match = re.search(r"\b(?:build|create|scaffold|generate|make|implement)\s+(?:an?\s+)?([a-z0-9 _\-]{1,64})\s+agent\b", text)
    if not match:
        return []

    raw_name = match.group(1).strip()
    # Convert to snake_case-ish.
    slug = re.sub(r"[^a-z0-9]+", "_", raw_name).strip("_")
    if not slug:
        return []

    return [f"{slug}_a2a_agent"]


async def _execute_system_tool_inline(
    tool_name: str,
    tool_registry,
    llm_client,
) -> tuple[dict | None, str | None]:
    """
    Execute a system tool inline and return (result, error).

    System tools are simple operations that should execute immediately
    in the chat flow, not through the backlog system.
    """
    if tool_name == "list_tools":
        # List all available tools including system tools, agents, and MCP tools
        system_tools = [
            {
                "name": "list_tools",
                "description": "List all available tools and their capabilities",
                "type": "system_tool",
            },
            {
                "name": "rediscover_agents",
                "description": "Scan the agents folder, analyze with LLM, and update registry.json",
                "type": "system_tool",
            },
            {
                "name": "list_agents",
                "description": "List all registered agents with their status",
                "type": "system_tool",
            },
            {
                "name": "schedule_task",
                "description": "Schedule a task to run at a specific time",
                "type": "system_tool",
            },
            {
                "name": "list_schedule",
                "description": "List all scheduled tasks",
                "type": "system_tool",
            },
        ]

        # Get registered MCP tools from registry
        mcp_tools = tool_registry.get_tools()
        mcp_tool_entries = [
            {
                "name": tool.get("name"),
                "description": tool.get("description", ""),
                "type": "mcp_tool",
            }
            for tool in mcp_tools
        ]

        return {
            "system_tools": system_tools,
            "mcp_tools": mcp_tool_entries,
            "total_count": len(system_tools) + len(mcp_tool_entries),
        }, None

    elif tool_name == "rediscover_agents":
        from ..tools.agent_discovery import discover_agents
        from pathlib import Path

        # Get agents directory
        project_root = Path(__file__).parent.parent.parent.parent
        agents_dir = project_root / "app" / "agents"

        logger.info("Executing system tool: rediscover_agents")

        result = await discover_agents(
            agents_dir=agents_dir,
            llm_client=llm_client,
            registry=tool_registry,
        )

        return result, None

    elif tool_name == "list_agents":
        # List all registered agents with their status
        agents = tool_registry.get_agents()
        all_agents = [
            {
                "name": agent.get("name"),
                "description": agent.get("description"),
                "status": agent.get("status", "unknown"),
                "transport": agent.get("transport"),
                "port": agent.get("port"),
                "endpoint": agent.get("endpoint"),
            }
            for agent in agents
        ]

        running = [a for a in agents if a.get("status") == "running"]

        return {
            "agents": all_agents,
            "running_count": len(running),
            "total_count": len(all_agents),
        }, None

    return None, f"Unknown system tool: {tool_name}"


def _format_tool_result(tool_name: str, result: dict) -> str:
    """Format a tool result as a human-readable response."""
    if tool_name == "list_tools":
        lines = ["**Available Tools:**\n"]

        system_tools = result.get("system_tools", [])
        if system_tools:
            lines.append("**System Tools:**")
            for tool in system_tools:
                lines.append(f"  • `{tool['name']}` - {tool['description']}")

        mcp_tools = result.get("mcp_tools", [])
        if mcp_tools:
            lines.append("\n**MCP Tools:**")
            for tool in mcp_tools:
                lines.append(f"  • `{tool['name']}` - {tool.get('description', 'No description')}")

        lines.append(f"\n**Total:** {result.get('total_count', 0)} tools available")
        return "\n".join(lines)

    elif tool_name == "rediscover_agents":
        found = result.get("agents_found", 0)
        registered = result.get("agents_registered", 0)
        skipped = result.get("agents_skipped", 0)
        errors = result.get("errors", [])

        lines = ["**Agent Discovery Complete:**"]
        lines.append(f"  • Found: {found} agents")
        lines.append(f"  • Registered: {registered}")
        lines.append(f"  • Skipped: {skipped}")
        if errors:
            lines.append(f"  • Errors: {', '.join(errors)}")
        return "\n".join(lines)

    elif tool_name == "list_agents":
        agents = result.get("agents", [])
        if not agents:
            return "**No agents registered.** Use `rediscover agents` to scan for agents."

        lines = ["**Registered Agents:**"]
        for agent in agents:
            status = agent.get("status", "unknown")
            endpoint = agent.get("endpoint", "N/A")
            lines.append(f"  • `{agent['name']}` [{status}]")
            lines.append(f"    - {agent.get('description', 'No description')}")
            lines.append(f"    - Endpoint: {endpoint}")

        lines.append(f"\n**Running:** {result.get('running_count', 0)}/{result.get('total_count', 0)}")
        return "\n".join(lines)

    # Default: return JSON-like representation
    import json
    return f"**Result:**\n```json\n{json.dumps(result, indent=2)}\n```"


def create_node(
    llm_client,
    tool_registry,
    state_manager=None,
) -> Callable[[ProgenitorState], Coroutine[Any, Any, ProgenitorState]]:
    """Create the decide node function.

    Args:
        llm_client: The LLM client for making decisions
        tool_registry: Registry of available tools and agents
        state_manager: Optional state manager for logging decisions
    """

    async def decide_node(state: ProgenitorState) -> ProgenitorState:
        """Make a routing decision for the current input."""
        user_input = state.get("current_input")

        if not user_input:
            # No input - check if we have backlog to process
            backlog = state.get("backlog", [])
            pending = [
                item for item in backlog
                if is_work_item_actionable(item)
            ]

            if pending:
                # Continue processing backlog
                return {"current_decision": None}

            return {"current_decision": None}

        # Get available capabilities
        capabilities = tool_registry.get_all_capabilities()

        # Get conversation history for context
        messages = state.get("messages", [])
        history = _extract_history(messages, limit=10)

        try:
            # Ask LLM for decision
            decision_data = await llm_client.decide(
                user_input=user_input,
                conversation_history=history,
                available_capabilities=capabilities,
            )

            # If the LLM returns a non-actionable direct answer (no direct_response)
            # but the user is explicitly asking to build an agent, force build routing.
            action_str = decision_data.get("action", "answer_directly")
            direct_response = decision_data.get("direct_response")

            # Check for list tools request first
            if _looks_like_list_tools_request(user_input):
                # Execute system tool inline and return direct response
                tool_name = "list_tools"
                result, error = await _execute_system_tool_inline(tool_name, tool_registry, llm_client)
                if error:
                    response_text = f"Error executing {tool_name}: {error}"
                else:
                    response_text = _format_tool_result(tool_name, result)

                decision = Decision(
                    action=DecisionAction.ANSWER_DIRECTLY,
                    reasoning="Executed list_tools system tool inline.",
                    requires_planning=False,
                    direct_response=response_text,
                )

                if state_manager:
                    await state_manager.log_decision({
                        "event": "system_tool_executed",
                        "tool_name": tool_name,
                        "timestamp": utcnow_isoformat(),
                    })

                return {"current_decision": decision}

            # Check for system tool requests (rediscover agents, etc.)
            elif _looks_like_agent_discovery_request(user_input):
                # Execute system tool inline and return direct response
                tool_name = "rediscover_agents"
                result, error = await _execute_system_tool_inline(tool_name, tool_registry, llm_client)
                if error:
                    response_text = f"Error executing {tool_name}: {error}"
                else:
                    response_text = _format_tool_result(tool_name, result)

                decision = Decision(
                    action=DecisionAction.ANSWER_DIRECTLY,
                    reasoning="Executed rediscover_agents system tool inline.",
                    requires_planning=False,
                    direct_response=response_text,
                )

                if state_manager:
                    await state_manager.log_decision({
                        "event": "system_tool_executed",
                        "tool_name": tool_name,
                        "timestamp": utcnow_isoformat(),
                    })

                return {"current_decision": decision}
            # Check for schedule requests (e.g., "remind me at 9am", "say hello every day")
            elif _looks_like_schedule_request(user_input):
                schedule_params = _extract_schedule_params(user_input)
                decision_data = {
                    "action": "schedule",
                    "reasoning": "User requested a scheduled/timed task.",
                    "requires_planning": True,  # Needs planner to process the instruction
                    "target_capabilities": ["schedule_task"],
                    "direct_response": None,
                    "schedule_params": schedule_params,  # Pass extracted params
                }
            elif (
                str(action_str).lower().strip() in ("answer", "answer_directly")
                and not direct_response
                and _looks_like_agent_build_request(user_input)
            ):
                decision_data = {
                    "action": "build_agent",
                    "reasoning": "User explicitly requested creation of a new agent.",
                    "requires_planning": True,
                    "target_capabilities": _infer_target_capabilities(user_input),
                    "direct_response": None,
                }

            # Parse decision
            action_str = decision_data.get("action", "answer_directly")
            try:
                action = DecisionAction(action_str)
            except ValueError:
                logger.warning("Unknown action '%s', falling back to answer_directly", action_str)
                action = DecisionAction.ANSWER_DIRECTLY

            decision = Decision(
                action=action,
                reasoning=decision_data.get("reasoning", ""),
                requires_planning=decision_data.get("requires_planning", False),
                target_capabilities=decision_data.get("target_capabilities", []),
                direct_response=decision_data.get("direct_response"),
            )

            # Log decision
            if state_manager:
                await state_manager.log_decision({
                    "event": "routing_decision",
                    "action": action.value,
                    "reasoning": decision_data.get("reasoning", ""),
                    "requires_planning": decision_data.get("requires_planning", False),
                    "target_capabilities": decision_data.get("target_capabilities", []),
                    "timestamp": utcnow_isoformat(),
                })

            return {"current_decision": decision}

        except (RateLimitError, APIConnectionError) as e:
            # Recoverable errors - log and raise for retry handling
            logger.warning("Recoverable LLM error in decide node: %s", e)
            raise LLMError(
                message=f"LLM API error: {e}",
                node_name="decide",
                context={"user_input": user_input[:100] if user_input else None},
                retry_after_seconds=5.0,
            ) from e

        except Exception as e:
            # Unexpected errors - log at error level, but still try to recover
            logger.error("Unexpected error in decide node: %s", e, exc_info=True)

            # Fallback to direct answer
            decision = Decision(
                action=DecisionAction.ANSWER_DIRECTLY,
                reasoning=f"Fallback due to error: {type(e).__name__}: {e}",
                requires_planning=False,
            )

            # Log the fallback
            if state_manager:
                await state_manager.log_decision({
                    "event": "routing_decision_fallback",
                    "error_type": type(e).__name__,
                    "error_message": str(e),
                    "timestamp": utcnow_isoformat(),
                })

            return {"current_decision": decision}

    return decide_node


def _extract_history(messages: list, limit: int = 10) -> list[dict[str, str]]:
    """Extract conversation history as list of dicts."""
    history = []
    for msg in messages[-limit:]:
        if isinstance(msg, dict):
            history.append({"role": msg.get("role", "user"), "content": msg.get("content", "")})
        elif hasattr(msg, "role"):
            history.append({"role": msg.role, "content": msg.content})
    return history
