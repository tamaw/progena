"""
Decide Node - Routes user input to appropriate action path.
"""

import json
import logging
from datetime import datetime
from typing import Callable, Coroutine, Any, Optional, cast, TYPE_CHECKING

import re

from openai import APIConnectionError, RateLimitError
from pydantic import BaseModel, Field

from ..state.models import ProgenitorState, Decision, DecisionAction, WorkItem
from ..state.helpers import is_work_item_actionable, get_ideal_state
from ..runtime.time_utils import utcnow_isoformat
from .errors import RecoverableError, LLMError, ValidationError
from ..tools.mcp import A2AClient

if TYPE_CHECKING:
    from ..state.execution_schemas import BuildSettings

logger = logging.getLogger(__name__)


class BuildNameExtraction(BaseModel):
    """Structured output for extracting build/agent name from user request."""

    project_name: str = Field(
        description="The extracted project/agent name in snake_case format. "
        "Should be a concise, descriptive name based on what the user wants to build. "
        "Examples: 'weather_agent', 'stock_tracker', 'email_assistant'. "
        "Do NOT include filler words like 'for_me', 'please', 'a', 'the'."
    )
    build_type: str = Field(
        description="The type of build: 'agent' for A2A agents, 'mcp' for MCP servers/tools."
    )
    reasoning: str = Field(
        description="Brief explanation of how the name was derived from the user request."
    )


def _looks_like_agent_build_request(user_input: str) -> bool:
    """Heuristic: detect explicit requests to build/create a new agent.

    This should only trigger for very explicit agent creation requests.
    The user must clearly express intent to BUILD/CREATE an AGENT.
    Questions about agents, general mentions, or ambiguous requests should NOT trigger this.
    """
    text = (user_input or "").lower()
    if not text.strip():
        return False

    # Explicit combined phrases that clearly indicate agent building intent
    # These are very specific to avoid false positives
    explicit_build_phrases = [
        "build an agent",
        "build a new agent",
        "build me an agent",
        "build an a2a agent",
        "build a2a agent",
        "build me an a2a agent",
        "create an agent",
        "create a new agent",
        "create me an agent",
        "create an a2a agent",
        "create a2a agent",
        "create me an a2a agent",
        "scaffold an agent",
        "scaffold a new agent",
        "scaffold an a2a agent",
        "generate an agent",
        "generate a new agent",
        "generate an a2a agent",
        "make an agent",
        "make a new agent",
        "make me an agent",
        "make an a2a agent",
        "implement an agent",
        "implement a new agent",
        "implement an a2a agent",
        # With specific agent names
        "build a hello world agent",
        "create a hello world agent",
        "build a weather agent",
        "create a weather agent",
    ]

    # Check for explicit phrases first
    if any(phrase in text for phrase in explicit_build_phrases):
        return True

    # Fallback: require BOTH strong build verb AND agent noun in close proximity
    # This is more strict than before
    import re

    # Pattern: build/create/scaffold + optional words + agent (but not just "agent" alone)
    build_agent_pattern = r'\b(build|create|scaffold|generate|implement)\s+(?:me\s+)?(?:a\s+|an\s+|the\s+)?(?:new\s+)?(?:[a-z]+\s+){0,3}agent\b'

    if re.search(build_agent_pattern, text):
        # Exclude false positives
        false_positives = [
            "build a plan",
            "build the plan",
            "build a docker",
            "build an image",
            "build the image",
            "build a container",
            "build the container",
            "what agent",
            "which agent",
            "about agent",
            "the agent",  # Referring to existing agent
            "your agent",
            "this agent",
        ]
        if any(fp in text for fp in false_positives):
            return False
        return True

    return False


def _looks_like_tool_build_request(user_input: str) -> bool:
    """Heuristic: detect explicit requests to build/create a new FastMCP tool.

    This should only trigger for very explicit tool creation requests.
    The user must clearly express intent to BUILD/CREATE a TOOL.
    """
    text = (user_input or "").lower()
    if not text.strip():
        return False

    # Explicit combined phrases that clearly indicate tool building intent
    explicit_build_phrases = [
        "build a tool",
        "build a new tool",
        "build me a tool",
        "build an mcp tool",
        "build a mcp tool",
        "build a fastmcp tool",
        "build an fastmcp tool",
        "create a tool",
        "create a new tool",
        "create me a tool",
        "create an mcp tool",
        "create a mcp tool",
        "create a fastmcp tool",
        "scaffold a tool",
        "scaffold a new tool",
        "generate a tool",
        "generate a new tool",
        "make a tool",
        "make a new tool",
        "make me a tool",
        "implement a tool",
        "implement a new tool",
    ]

    # Check for explicit phrases first
    if any(phrase in text for phrase in explicit_build_phrases):
        return True

    # Pattern: build/create/scaffold + optional words + tool
    import re
    build_tool_pattern = r'\b(build|create|scaffold|generate|implement)\s+(?:me\s+)?(?:a\s+|an\s+|the\s+)?(?:new\s+)?(?:[a-z]+\s+){0,3}(?:mcp\s+)?tool\b'

    if re.search(build_tool_pattern, text):
        # Exclude false positives
        false_positives = [
            "use a tool",
            "use the tool",
            "what tool",
            "which tool",
            "about tool",
            "the tool",  # Referring to existing tool
            "your tool",
            "this tool",
            "list tool",
        ]
        if any(fp in text for fp in false_positives):
            return False
        return True

    return False


def _looks_like_agent_discovery_request(user_input: str) -> bool:
    """Heuristic: detect requests to rediscover/rewire agents."""
    text = (user_input or "").lower()
    if not text.strip():
        return False

    # Direct compound phrases that clearly indicate discovery intent
    direct_discovery_phrases = (
        "rewire agents",
        "rewire the agents",
        "rediscover agents",
        "rescan agents",
        "refresh agents",
        "reload agents",
        "scan agents",
        "discover agents",
        "update registry",
        "refresh registry",
        "sync agents",
        "re-scan agents",
        "re-discover agents",
        # Key pattern: "agent discovery" as a compound noun
        "agent discovery",
        "run agent discovery",
        "run discovery",
        "do agent discovery",
        "start agent discovery",
        "perform agent discovery",
        "trigger agent discovery",
        "execute agent discovery",
    )

    if any(phrase in text for phrase in direct_discovery_phrases):
        return True

    # Single-word discovery verbs that imply agents when "agent" is present
    has_discovery_verb = any(
        verb in text
        for verb in (
            "rewire",
            "rediscover",
            "rescan",
            "re-scan",
            "re-discover",
            "refresh",
            "reload",
        )
    )

    has_agent_context = any(
        phrase in text
        for phrase in (
            "agent",
            "registry",
        )
    )

    return has_discovery_verb and has_agent_context


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


def _looks_like_build_status_request(user_input: str) -> bool:
    """Heuristic: detect requests to check build status/progress."""
    text = (user_input or "").lower()
    if not text.strip():
        return False

    # Direct patterns for build status
    direct_patterns = [
        "build status",
        "build progress",
        "build state",
        "what are you building",
        "what's being built",
        "current build",
        "is there a build",
        "check build",
        "building status",
        "how is the build",
        "how's the build",
        "build update",
        "update on build",
        "building progress",
        "are you building",
    ]

    return any(pattern in text for pattern in direct_patterns)


def _looks_like_current_time_request(user_input: str) -> bool:
    """Heuristic: detect requests for the current time (UTC)."""
    text = (user_input or "").lower()
    if not text.strip():
        return False

    direct_patterns = [
        "current time",
        "utc time",
        "time now",
        "what time is it",
        "tell me the time",
        "tell me the current time",
        "what is the time",
        "whats the time",
        "what's the time",
    ]

    return any(pattern in text for pattern in direct_patterns)

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


def _looks_like_list_schedule_request(user_input: str) -> bool:
    """Heuristic: detect requests to list/view scheduled tasks."""
    text = (user_input or "").lower()
    if not text.strip():
        return False

    # Direct patterns for listing schedule
    direct_patterns = [
        "what is your schedule",
        "what's your schedule",
        "whats your schedule",
        "show schedule",
        "show my schedule",
        "show the schedule",
        "list schedule",
        "list schedules",
        "list scheduled",
        "view schedule",
        "view schedules",
        "scheduled tasks",
        "what is scheduled",
        "what's scheduled",
        "whats scheduled",
        "any scheduled",
        "pending schedule",
        "upcoming tasks",
        "upcoming schedule",
        "my reminders",
        "show reminders",
        "list reminders",
        "what reminders",
    ]

    return any(pattern in text for pattern in direct_patterns)


def _looks_like_self_reflect_request(user_input: str) -> bool:
    """Heuristic: detect requests for self-reflection/introspection."""
    text = (user_input or "").lower()
    if not text.strip():
        return False

    # Direct self-reflection patterns
    direct_patterns = [
        "how do you work",
        "how do you handle",
        "how do you process",
        "what is your",
        "what are your",
        "explain your",
        "describe your",
        "your source code",
        "your implementation",
        "your architecture",
        "how are you built",
        "how were you built",
        "how are you implemented",
        "self reflect",
        "self-reflect",
        "introspect",
        "understand yourself",
        "examine yourself",
        "look at your code",
        "read your code",
        "your own code",
        "your capabilities",
        "what can you do",
    ]

    # Check for self-referential questions about internals
    has_self_reference = any(
        phrase in text
        for phrase in (
            "yourself",
            "your code",
            "your source",
            "your implementation",
            "your internal",
            "how you ",
            "what you ",
        )
    )

    has_introspection_intent = any(
        phrase in text
        for phrase in (
            "work",
            "function",
            "implement",
            "process",
            "handle",
            "built",
            "design",
            "architecture",
        )
    )

    return any(pattern in text for pattern in direct_patterns) or (
        has_self_reference and has_introspection_intent
    )


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


def _clean_project_name(name: str) -> str:
    """Clean and normalize a project name to snake_case."""
    if not name:
        return ""

    # Lowercase and replace non-alphanumeric with underscores
    cleaned = re.sub(r'[^a-z0-9_]', '_', name.lower()).strip('_')

    # Remove common filler words that might have slipped through
    filler_patterns = [
        r'^for_me_',
        r'^please_',
        r'^a_',
        r'^an_',
        r'^the_',
        r'^new_',
        r'^build_',
        r'^create_',
        r'_for_me$',
        r'_please$',
    ]
    for pattern in filler_patterns:
        cleaned = re.sub(pattern, '', cleaned)

    # Remove double underscores
    cleaned = re.sub(r'_+', '_', cleaned).strip('_')

    return cleaned


async def _generate_build_settings(
    user_input: str,
    llm_client,
    llm_action: str,
    decision_data: dict | None = None,
) -> "BuildSettings":
    """Generate structured BuildSettings, using decision data if available.

    BL-007: Uses structured output to determine:
    - build_type: agent or mcp
    - project_name: derived from user request for repeatability
    - directories: automatically derived from type + name

    Args:
        user_input: Original user request
        llm_client: LLM client for structured generation (fallback)
        llm_action: The action from initial LLM decision
        decision_data: The full decision response from the LLM (may contain project_name, build_type, build_description)

    Returns:
        BuildSettings with all derived fields populated
    """
    from ..state.execution_schemas import BuildSettings, BuildType
    import uuid

    build_id = f"build_{uuid.uuid4().hex[:12]}"

    project_name = None
    build_type = BuildType.AGENT
    build_description = None

    # Primary: Use values from the decision response if available
    # This avoids an extra LLM call since decide() already extracted these
    if decision_data:
        decision_project_name = decision_data.get("project_name")
        decision_build_type = decision_data.get("build_type")
        decision_build_description = decision_data.get("build_description")

        if decision_project_name:
            project_name = _clean_project_name(decision_project_name)
            if project_name:
                logger.info("Using project_name from decision response: %s", project_name)

        if decision_build_type:
            if decision_build_type.lower() == "mcp":
                build_type = BuildType.MCP
            logger.info("Using build_type from decision response: %s", build_type)

        if decision_build_description:
            build_description = decision_build_description
            logger.info("Using build_description from decision response: %s", build_description[:100] if build_description else "None")

    # Fallback: Use LLM structured output if decision didn't provide project_name
    if not project_name and llm_client:
        try:
            from ..llm.client import _build_json_schema_format

            system_prompt = """You are an expert at extracting project names from user requests.

When a user asks to build an agent or MCP server, extract a clean, descriptive name.

RULES:
1. Extract the CORE PURPOSE/TOPIC, not filler words
2. Use snake_case format (lowercase with underscores)
3. Keep names concise but descriptive (2-4 words max)
4. NEVER include: 'for_me', 'please', 'a', 'an', 'the', 'new', 'build', 'create'

EXAMPLES:
- "Build for me a weather agent" → weather_agent
- "Create an agent that tracks stocks" → stock_tracker
- "Build me a new email assistant agent please" → email_assistant
- "I want an agent to help with calendar scheduling" → calendar_scheduler
- "Make a simple todo list agent" → todo_list
- "Build an MCP server for GitHub integration" → github_integration"""

            prompt = f"""Extract the project name from this user request:

"{user_input}"

Return the extracted name and build type."""

            # Try structured output first
            response_format = llm_client._get_json_response_format(
                BuildNameExtraction, "build_name_extraction"
            )

            # Add JSON instruction if using json_object format (Azure older API versions)
            actual_prompt = prompt
            if response_format.get("type") == "json_object":
                actual_prompt = prompt + "\n\nRespond with valid JSON matching this schema: {project_name: string, build_type: 'agent'|'mcp', reasoning: string}"

            response = await llm_client.complete(
                prompt=actual_prompt,
                system_prompt=system_prompt,
                temperature=0.1,
                response_format=response_format,
            )

            if response:
                try:
                    # Parse the structured response
                    parsed = json.loads(response.strip())
                    extracted_name = _clean_project_name(parsed.get("project_name", ""))
                    extracted_type = parsed.get("build_type", "agent").strip().lower()
                    reasoning = parsed.get("reasoning", "")

                    if extracted_name and len(extracted_name) > 1:
                        project_name = extracted_name
                        logger.info(
                            "LLM extracted project name: %s (reasoning: %s)",
                            project_name, reasoning
                        )

                    if extracted_type == "mcp":
                        build_type = BuildType.MCP

                except json.JSONDecodeError:
                    logger.warning("Failed to parse LLM structured response: %s", response[:100])

        except Exception as e:
            logger.warning("Failed to extract project name via LLM structured output: %s", e)

    # Fallback: Try regex patterns if LLM failed
    if not project_name:
        text = user_input.lower()

        # Improved patterns that skip filler words
        name_patterns = [
            # "build/create a <adjective>? <name> agent" - skip "for me", "please" etc.
            r'\b(?:build|create|scaffold|generate|make|implement)\s+(?:for\s+me\s+)?(?:me\s+)?(?:please\s+)?(?:a\s+|an\s+)?(?:new\s+)?(?:simple\s+|basic\s+)?([a-z][a-z0-9]*(?:\s+[a-z][a-z0-9]*)?)\s+(?:a2a\s+)?(?:agent|mcp)',
            # "agent named/called <name>"
            r'\bagent\s+(?:named?|called?)\s+([a-z0-9_\-]+)',
            # "named/called <name>"
            r'\b(?:named?|called?)\s+([a-z0-9_\-]+)',
        ]

        for pattern in name_patterns:
            match = re.search(pattern, text)
            if match:
                raw_name = match.group(1).strip()
                # Filter out common words that aren't names
                skip_words = {"a", "an", "the", "new", "simple", "basic", "for", "me", "please"}
                if raw_name and raw_name not in skip_words:
                    # Convert to snake_case
                    project_name = re.sub(r'\s+', '_', raw_name)
                    logger.info("Regex extracted project name: %s", project_name)
                    break

    # Determine build type from text if not already set
    text = user_input.lower()
    if build_type == BuildType.AGENT and ("mcp" in text or "mcp server" in text or "mcp tool" in text):
        build_type = BuildType.MCP

    # Final fallback: extract meaningful words
    if not project_name:
        # Use meaningful words from request, excluding common filler
        exclude_words = {
            'build', 'create', 'make', 'scaffold', 'generate', 'implement',
            'an', 'a', 'the', 'me', 'for', 'please', 'new', 'simple', 'basic',
            'agent', 'mcp', 'server', 'tool', 'want', 'need', 'would', 'like',
            'can', 'you', 'i', 'that', 'which', 'to', 'with'
        }
        words = [w for w in re.findall(r'[a-z]+', text) if w not in exclude_words and len(w) > 2]
        if words:
            project_name = '_'.join(words[:2])
            logger.info("Fallback extracted project name from words: %s", project_name)
        else:
            project_name = f"project_{uuid.uuid4().hex[:6]}"
            logger.info("Using random project name: %s", project_name)

    # Use build_description if available, otherwise generate one
    description = build_description or f"Build {build_type.value}: {project_name}"

    # Create BuildSettings with all derived fields
    return BuildSettings.create_from_llm_output(
        build_id=build_id,
        project_name=project_name,
        build_type=build_type,
        user_request=user_input,
        description=description,
    )


async def _execute_system_tool_inline(
    tool_name: str,
    tool_registry,
    llm_client,
    state: "ProgenitorState | None" = None,
    state_manager=None,
) -> tuple[dict[str, Any] | None, str | None]:
    """
    Execute a system tool inline and return (result, error).

    Args:
        tool_name: Name of the system tool to execute.
        tool_registry: Registry of available tools and agents.
        llm_client: LLM client for tools that need AI capabilities.
        state: Current graph state (in-memory).
        state_manager: StateManager for reading persisted state files.

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
                "name": "build_status",
                "description": "Check the current build status, progress, and messages",
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
                "description": "List all scheduled tasks and reminders",
                "type": "system_tool",
            },
            {
                "name": "current_time",
                "description": "Return the current UTC time",
                "type": "system_tool",
            },
            {
                "name": "self_reflect",
                "description": "Introspect own source code to understand how Progenitor works",
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

    elif tool_name == "build_status":
        # Return current build status - check background runner first
        from ..runtime.background_build import get_background_runner

        runner = get_background_runner()

        if runner:
            # Get status from background runner
            build_result = runner.get_build_status()

            if build_result:
                result = {
                    "build_in_progress": build_result.status.value in ("pending", "running"),
                    "build_id": build_result.build_id,
                    "status": build_result.status.value,
                    "task_description": build_result.task_description,
                    "started_at": build_result.started_at.isoformat() if build_result.started_at else None,
                    "completed_at": build_result.completed_at.isoformat() if build_result.completed_at else None,
                    "iterations": build_result.iterations,
                    "verification_score": build_result.verification_score,
                    "output_summary": build_result.output_summary,
                    "error_message": build_result.error_message,
                    "agent_name": build_result.agent_name,
                    "success_criteria_met": build_result.success_criteria_met,
                    "success_criteria_unmet": build_result.success_criteria_unmet,
                }

                # Get recent builds
                recent_builds = runner.get_recent_builds(limit=3)
                if len(recent_builds) > 1:
                    result["recent_builds"] = [
                        {
                            "build_id": b.build_id,
                            "status": b.status.value,
                            "task_description": b.task_description[:50] + "..." if len(b.task_description) > 50 else b.task_description,
                        }
                        for b in recent_builds[:-1]  # Exclude current
                    ]

                return result, None

        # Read from persisted state files via state_manager
        persisted_state = None
        if state_manager:
            try:
                persisted_state = await state_manager.get_build_state()
            except Exception as e:
                logger.warning("Failed to read persisted state: %s", e)

        # Merge in-memory state with persisted state (in-memory takes priority)
        effective_state = {}
        if persisted_state:
            effective_state.update(persisted_state)
        if state:
            effective_state.update(state)

        if not effective_state:
            return {
                "build_in_progress": False,
                "message": "No build in progress and no recent builds found.",
            }, None

        build_in_progress = effective_state.get("build_in_progress", False)
        build_phase = effective_state.get("build_phase")
        build_task = effective_state.get("build_task_description")
        build_started = effective_state.get("build_started_at")
        build_id = (effective_state.get("build_settings") or {}).get("build_id")
        current_iteration = effective_state.get("iteration", {}).get("current", 0)
        verification_score = effective_state.get("verification_score", 0)
        build_messages = effective_state.get("build_messages", [])
        ideal_state = get_ideal_state(effective_state)

        # Check for last build result (completed async builds store this)
        last_result = effective_state.get("last_build_result")

        # Get the last few build messages
        recent_messages = build_messages[-5:] if build_messages else []

        result = {
            "build_in_progress": build_in_progress,
            "build_id": build_id,
            "phase": build_phase,
            "task_description": build_task,
            "started_at": build_started,
            "current_iteration": current_iteration,
            "verification_score": verification_score,
            "message_count": len(build_messages),
            "recent_messages": recent_messages,
        }

        ideal_state_dict = None
        if ideal_state:
            if isinstance(ideal_state, dict):
                ideal_state_dict = ideal_state
            elif hasattr(ideal_state, "model_dump"):
                ideal_state_dict = ideal_state.model_dump()

        if ideal_state_dict:
            result["target_score"] = ideal_state_dict.get("stop_conditions", [{}])[0].get("threshold", 0.8)
            result["success_criteria"] = [
                c.get("description") for c in ideal_state_dict.get("success_criteria", [])
            ]

        return result, None

    elif tool_name == "self_reflect":
        # Self-reflection tool - introspect progenitor's own source code
        from ..tools.self_reflect import SelfReflection

        logger.info("Executing system tool: self_reflect")
        reflection = SelfReflection(llm_client=llm_client)

        # For inline execution, return module listing
        # Full reflection with specific queries should use execute_self_reflect
        modules = reflection.list_modules()

        return {
            "description": "Progenitor self-reflection - exploring own source code",
            "root_path": str(reflection.root),
            "modules": [
                {
                    "name": m.name,
                    "path": m.path,
                    "is_directory": m.is_directory,
                    "description": m.description,
                }
                for m in modules
            ],
            "module_count": len([m for m in modules if m.is_directory]),
            "file_count": len([m for m in modules if not m.is_directory]),
            "tip": "Use follow-up questions to explore specific modules or search for code patterns.",
        }, None

    elif tool_name == "current_time":
        # Return current UTC time
        return {
            "utc_time": utcnow_isoformat(),
            "timezone": "UTC",
        }, None

    elif tool_name == "list_schedule":
        # List all scheduled tasks
        if state is None:
            return {
                "scheduled_tasks": [],
                "total_count": 0,
                "message": "No state available - schedule information unavailable",
            }, None

        schedule = state.get("schedule", [])

        # Convert schedule entries to readable format
        tasks = []
        for item in schedule:
            if hasattr(item, "model_dump"):
                item_dict = item.model_dump()
            elif isinstance(item, dict):
                item_dict = item
            else:
                continue

            tasks.append({
                "id": item_dict.get("id"),
                "description": item_dict.get("description", "No description"),
                "cron_expression": item_dict.get("cron_expression"),
                "next_run": item_dict.get("next_run"),
                "action_type": item_dict.get("action_type"),
                "enabled": item_dict.get("enabled", True),
            })

        return {
            "scheduled_tasks": tasks,
            "total_count": len(tasks),
        }, None

    return None, f"Unknown system tool: {tool_name}"


async def _execute_a2a_agent_inline(
    target_capabilities: list[str],
    user_input: str,
    tool_registry,
    timeout: float = 60.0,
) -> tuple[dict | None, str | None]:
    """Execute an A2A agent call inline without going through backlog.

    Args:
        target_capabilities: List of capabilities to match agent
        user_input: The user's request to send to the agent
        tool_registry: Registry to find agent by capability
        timeout: Request timeout in seconds

    Returns:
        Tuple of (result_dict, error_string)
    """
    if not target_capabilities:
        return None, "No target capabilities specified"

    # Find agent by first matching capability
    capability = target_capabilities[0]
    agent_config = tool_registry.find_agent_by_capability(capability)

    if not agent_config:
        return None, f"No agent found for capability: {capability}"

    agent_name = agent_config.get("name", "unknown")
    status = agent_config.get("status", "stopped")

    # Check agent status
    if status == "failed":
        error = agent_config.get("last_error", "Unknown error")
        return None, f"Agent '{agent_name}' is in failed state: {error}"

    if status not in ("running", "waiting", "executing"):
        return None, f"Agent '{agent_name}' is not running (status: {status})"

    # Call the agent
    a2a_client = A2AClient()
    result = await a2a_client.call_agent(
        agent_config,
        request=user_input,
        timeout=timeout,
    )

    return result, None


async def _execute_schedule_task_inline(
    user_input: str,
    schedule_params: dict[str, Any],
    llm_client,
    state: "ProgenitorState | None" = None,
) -> tuple[dict[str, Any] | None, str | None, dict[str, Any] | None]:
    """
    Execute a schedule task inline using the LLM to process the instruction.

    Returns:
        Tuple of (result_dict, error_string, schedule_entry_data)
    """
    from ..tools.schedule import add_scheduled_task

    # Use LLM to extract and process schedule parameters
    system_prompt = """You are a scheduling assistant. Extract and format a scheduled task from the user's request.

Return JSON with:
{
    "time_expression": "<when to run, e.g. 'in 5 minutes', 'at 9am', 'every day at 10am'>",
    "instruction": "<clear instruction for what to do at that time>",
    "description": "<brief human-readable description of the task>"
}

Examples:
- "remind me to call John in 30 minutes" → time: "in 30 minutes", instruction: "Remind the user to call John", description: "Reminder: Call John"
- "say hello world at 9am" → time: "at 9am", instruction: "Say 'Hello World!' to the user", description: "Say hello world"
- "every day at 5pm tell me a joke" → time: "every day at 5pm", instruction: "Tell the user a joke", description: "Daily joke"
"""

    messages = [{"role": "user", "content": f"Schedule request: {user_input}"}]

    try:
        response = await llm_client.chat(
            messages=messages,
            system_prompt=system_prompt,
            temperature=0.3,
            response_format={"type": "json_object"},
        )

        parsed = json.loads(response)

        time_expr = parsed.get("time_expression", schedule_params.get("time_expression", ""))
        instruction = parsed.get("instruction", user_input)
        description = parsed.get("description", f"Scheduled: {user_input[:50]}")

        if not time_expr:
            return None, "Could not extract time from your request. Please specify when (e.g., 'in 5 minutes', 'at 9am').", None

        # Add the scheduled task
        result, entry_data = await add_scheduled_task(
            user_request=user_input,
            time_expression=time_expr,
            instruction=instruction,
            description=description,
        )

        if result.success:
            return {
                "success": True,
                "schedule_id": result.schedule_id,
                "description": description,
                "instruction": instruction,
                "next_run": result.next_run,
                "recurring": "repeat_minutes" in entry_data if entry_data else False,
            }, None, entry_data
        else:
            return None, result.message, None

    except json.JSONDecodeError as e:
        logger.warning("Failed to parse schedule LLM response: %s", e)
        return None, f"Failed to process schedule request: {e}", None
    except Exception as e:
        logger.error("Error executing schedule task: %s", e, exc_info=True)
        return None, f"Error scheduling task: {e}", None


async def _format_schedule_result(result: dict, llm_client) -> str:
    """Format a schedule task result as a short, plain-English response."""
    instruction = result.get("instruction", "complete your scheduled request")
    prompt = (
        "Create a short, friendly confirmation for a scheduled task. "
        "Do not include IDs or exact timestamps. Do not use markdown. "
        "Focus on what will happen.\n\n"
        f"Task details: {json.dumps(result, ensure_ascii=False)}"
    )

    try:
        response = await llm_client.complete(
            prompt=prompt,
            system_prompt="You write concise confirmations for scheduled tasks.",
            temperature=0.2,
        )
        if response:
            return response.strip()
    except Exception:
        pass

    return f"Got it. I'll {instruction}."


def _format_a2a_response(result: dict, agent_name: str) -> str:
    """Format an A2A agent response for display.

    Args:
        result: The result dictionary from A2AClient.call_agent()
        agent_name: Name of the agent for error messages

    Returns:
        Human-readable response string
    """
    if result.get("success", False):
        response = result.get("response") or result.get("result") or result.get("data")
        if isinstance(response, dict):
            # Format dict as readable text
            return str(response)
        return str(response) if response else "Agent completed successfully."
    else:
        error = result.get("error", "Unknown error")
        return f"Agent '{agent_name}' error: {error}"


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

    elif tool_name == "build_status":
        build_in_progress = result.get("build_in_progress", False)
        status = result.get("status", "unknown")

        # Handle completed/failed builds
        if status in ("completed", "failed", "cancelled"):
            status_emoji = "✓" if status == "completed" else "✗" if status == "failed" else "○"
            lines = [f"**{status_emoji} Build {status.title()}**"]
            lines.append(f"  • **Task:** {result.get('task_description', 'No description')}")
            lines.append(f"  • **Build ID:** `{result.get('build_id', 'unknown')}`")
            lines.append(f"  • **Started:** {result.get('started_at', 'unknown')}")
            lines.append(f"  • **Completed:** {result.get('completed_at', 'unknown')}")
            lines.append(f"  • **Iterations:** {result.get('iterations', 0)}")
            lines.append(f"  • **Final Score:** {result.get('verification_score', 0):.2f}")

            if result.get("agent_name"):
                lines.append(f"  • **Agent Built:** `{result['agent_name']}`")

            if result.get("output_summary"):
                lines.append(f"\n**Summary:** {result['output_summary']}")

            if result.get("error_message"):
                lines.append(f"\n**Error:** {result['error_message']}")

            # Show criteria results
            met = result.get("success_criteria_met", [])
            unmet = result.get("success_criteria_unmet", [])
            if met:
                lines.append(f"\n**✓ Criteria Met ({len(met)}):**")
                for c in met[:5]:
                    lines.append(f"  • {c}")
            if unmet:
                lines.append(f"\n**○ Criteria Unmet ({len(unmet)}):**")
                for c in unmet[:5]:
                    lines.append(f"  • {c}")

            return "\n".join(lines)

        if not build_in_progress:
            # Check for message indicating no builds
            message = result.get("message")
            if message:
                return f"**📋 Build Status**\n\n{message}"
            return "**No build in progress.** The system is idle and ready for new build requests."

        # Running build
        lines = ["**🔨 Build In Progress**"]
        lines.append(f"  • **Task:** {result.get('task_description', 'No description')}")
        lines.append(f"  • **Build ID:** `{result.get('build_id', 'unknown')}`")
        lines.append(f"  • **Status:** {status}")
        lines.append(f"  • **Started:** {result.get('started_at', 'unknown')}")
        lines.append(f"  • **Iterations:** {result.get('iterations', 0)}")
        lines.append(f"  • **Current Score:** {result.get('verification_score', 0):.2f}")

        # Show phase if available (sync builds)
        phase = result.get("phase")
        if phase:
            lines.append(f"  • **Phase:** {phase}")

        criteria = result.get("success_criteria", [])
        if criteria:
            lines.append("\n**Success Criteria:**")
            for c in criteria:
                lines.append(f"  • {c}")

        recent = result.get("recent_messages", [])
        if recent:
            lines.append(f"\n**Recent Activity ({result.get('message_count', 0)} total messages):**")
            for msg in recent[-3:]:
                role = msg.get("role", "unknown")
                content = msg.get("content", "")[:100]
                if len(msg.get("content", "")) > 100:
                    content += "..."
                lines.append(f"  [{role}] {content}")

        return "\n".join(lines)

    elif tool_name == "self_reflect":
        modules = result.get("modules", [])
        dirs = [m for m in modules if m.get("is_directory")]
        files = [m for m in modules if not m.get("is_directory")]

        lines = ["**🔍 Progenitor Self-Reflection**\n"]
        lines.append(f"Source location: `{result.get('root_path', 'unknown')}`\n")

        if dirs:
            lines.append("**Modules (directories):**")
            for d in dirs:
                lines.append(f"  • `{d['name']}/`")

        if files:
            lines.append("\n**Root files:**")
            for f in files:
                desc = f" - {f['description']}" if f.get('description') else ""
                lines.append(f"  • `{f['name']}`{desc}")

        lines.append(f"\n**Summary:** {result.get('module_count', 0)} modules, {result.get('file_count', 0)} root files")

        tip = result.get("tip")
        if tip:
            lines.append(f"\n💡 *{tip}*")

        return "\n".join(lines)

    elif tool_name == "current_time":
        utc_time = result.get("utc_time")
        if not utc_time:
            return "Unable to determine current UTC time."
        return f"Current UTC time: {utc_time}"

    elif tool_name == "list_schedule":
        tasks = result.get("scheduled_tasks", [])

        if not tasks:
            return "**📅 No Scheduled Tasks**\n\nYou don't have any scheduled tasks or reminders set up.\n\n💡 *You can schedule tasks by saying things like 'remind me at 9am to check emails' or 'every day at 5pm run a status check'.*"

        lines = ["**📅 Scheduled Tasks:**\n"]
        for task in tasks:
            enabled = "✓" if task.get("enabled", True) else "○"
            lines.append(f"  {enabled} **{task.get('description', 'Unnamed task')}**")
            if task.get("cron_expression"):
                lines.append(f"      Schedule: `{task['cron_expression']}`")
            if task.get("next_run"):
                lines.append(f"      Next run: {task['next_run']}")
            if task.get("action_type"):
                lines.append(f"      Action: {task['action_type']}")
            lines.append("")  # Blank line between tasks

        lines.append(f"**Total:** {result.get('total_count', 0)} scheduled task(s)")
        return "\n".join(lines)

    # Default: return JSON-like representation
    import json
    return f"**Result:**\n```json\n{json.dumps(result, indent=2)}\n```"


# Default confidence threshold (used when config not provided)
DEFAULT_BUILD_CONFIDENCE_THRESHOLD = 0.95


def create_node(
    llm_client,
    tool_registry,
    state_manager=None,
    config=None,
) -> Callable[[ProgenitorState], Coroutine[Any, Any, ProgenitorState]]:
    """Create the decide node function.

    Args:
        llm_client: The LLM client for making decisions
        tool_registry: Registry of available tools and agents
        state_manager: Optional state manager for logging decisions
        config: Optional ProgenitorConfig for runtime settings
    """
    # Get build confidence threshold from config or use default
    build_confidence_threshold = DEFAULT_BUILD_CONFIDENCE_THRESHOLD
    if config and hasattr(config, 'runtime'):
        build_confidence_threshold = getattr(
            config.runtime, 'build_confidence_threshold', DEFAULT_BUILD_CONFIDENCE_THRESHOLD
        )

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

        # ============================================================
        # FAST-PATH: Handle system tools without LLM call
        # These heuristics are very specific and avoid false positives
        # ============================================================

        # Check for list tools request (fast-path - no LLM needed)
        if _looks_like_list_tools_request(user_input):
            tool_name = "list_tools"
            result, error = await _execute_system_tool_inline(tool_name, tool_registry, llm_client)
            if error or result is None:
                response_text = f"Error executing {tool_name}: {error or 'No result'}"
            else:
                response_text = _format_tool_result(tool_name, result)

            decision = Decision(
                action=DecisionAction.ANSWER_DIRECTLY,
                reasoning="Fast-path: list_tools system tool (no LLM needed).",
                requires_planning=False,
                direct_response=response_text,
            )

            if state_manager:
                await state_manager.log_decision({
                    "event": "system_tool_executed_fast_path",
                    "tool_name": tool_name,
                    "timestamp": utcnow_isoformat(),
                })

            return {"current_decision": decision}

        # Check for list schedule requests (fast-path - no LLM needed)
        if _looks_like_list_schedule_request(user_input):
            tool_name = "list_schedule"
            result, error = await _execute_system_tool_inline(
                tool_name, tool_registry, llm_client, state, state_manager
            )
            if error or result is None:
                response_text = f"Error executing {tool_name}: {error or 'No result'}"
            else:
                response_text = _format_tool_result(tool_name, result)

            decision = Decision(
                action=DecisionAction.ANSWER_DIRECTLY,
                reasoning="Fast-path: list_schedule system tool (no LLM needed).",
                requires_planning=False,
                direct_response=response_text,
            )

            if state_manager:
                await state_manager.log_decision({
                    "event": "system_tool_executed_fast_path",
                    "tool_name": tool_name,
                    "timestamp": utcnow_isoformat(),
                })

            return {"current_decision": decision}

        # Check for current time requests (fast-path - no LLM needed)
        if _looks_like_current_time_request(user_input):
            tool_name = "current_time"
            result, error = await _execute_system_tool_inline(
                tool_name, tool_registry, llm_client, state, state_manager
            )
            if error or result is None:
                response_text = f"Error executing {tool_name}: {error or 'No result'}"
            else:
                response_text = _format_tool_result(tool_name, result)

            decision = Decision(
                action=DecisionAction.ANSWER_DIRECTLY,
                reasoning="Fast-path: current_time system tool (no LLM needed).",
                requires_planning=False,
                direct_response=response_text,
            )

            if state_manager:
                await state_manager.log_decision({
                    "event": "system_tool_executed_fast_path",
                    "tool_name": tool_name,
                    "timestamp": utcnow_isoformat(),
                })

            return {"current_decision": decision}

        # Check for build status requests (fast-path - no LLM needed)
        if _looks_like_build_status_request(user_input):
            tool_name = "build_status"
            result, error = await _execute_system_tool_inline(
                tool_name, tool_registry, llm_client, state, state_manager
            )
            if error or result is None:
                response_text = f"Error executing {tool_name}: {error or 'No result'}"
            else:
                response_text = _format_tool_result(tool_name, result)

            decision = Decision(
                action=DecisionAction.ANSWER_DIRECTLY,
                reasoning="Fast-path: build_status system tool (no LLM needed).",
                requires_planning=False,
                direct_response=response_text,
            )

            if state_manager:
                await state_manager.log_decision({
                    "event": "system_tool_executed_fast_path",
                    "tool_name": tool_name,
                    "timestamp": utcnow_isoformat(),
                })

            return {"current_decision": decision}

        # Check for agent discovery requests (fast-path - no LLM needed)
        if _looks_like_agent_discovery_request(user_input):
            tool_name = "rediscover_agents"
            result, error = await _execute_system_tool_inline(tool_name, tool_registry, llm_client)
            if error or result is None:
                response_text = f"Error executing {tool_name}: {error or 'No result'}"
            else:
                response_text = _format_tool_result(tool_name, result)

            decision = Decision(
                action=DecisionAction.ANSWER_DIRECTLY,
                reasoning="Fast-path: rediscover_agents system tool (no LLM needed).",
                requires_planning=False,
                direct_response=response_text,
            )

            if state_manager:
                await state_manager.log_decision({
                    "event": "system_tool_executed_fast_path",
                    "tool_name": tool_name,
                    "timestamp": utcnow_isoformat(),
                })

            return {"current_decision": decision}

        # Check for schedule requests (fast-path - uses LLM for parsing but no routing decision)
        if _looks_like_schedule_request(user_input):
            schedule_params = _extract_schedule_params(user_input)

            result, error, entry_data = await _execute_schedule_task_inline(
                user_input, schedule_params, llm_client, state
            )

            if error or result is None:
                response_text = f"⚠️ **Scheduling Failed**\n\n{error or 'No result'}"
            else:
                response_text = await _format_schedule_result(result, llm_client)

            decision = Decision(
                action=DecisionAction.ANSWER_DIRECTLY,
                reasoning="Fast-path: schedule_task (no routing LLM needed).",
                requires_planning=False,
                direct_response=response_text,
            )

            if state_manager:
                await state_manager.log_decision({
                    "event": "system_tool_executed_fast_path",
                    "tool_name": "schedule_task",
                    "schedule_id": result.get("schedule_id") if result else None,
                    "timestamp": utcnow_isoformat(),
                })

            # Return the decision AND the schedule entry to add to state
            result_state: dict[str, Any] = {"current_decision": decision}
            if entry_data:
                from ..state.models import ScheduleEntry
                new_entry = ScheduleEntry(**entry_data)
                current_schedule = state.get("schedule", []) if state else []
                result_state["schedule"] = list(current_schedule) + [new_entry]

                # Persist the new schedule entry to schedule.json
                if state_manager:
                    await state_manager.add_schedule_entry(new_entry)

                logger.info("Added schedule entry %s to state and persisted (fast-path)", entry_data.get("id"))

            return cast(ProgenitorState, result_state)

        # Check for self-reflection requests (fast-path - no LLM needed for routing)
        if _looks_like_self_reflect_request(user_input):
            tool_name = "self_reflect"
            result, error = await _execute_system_tool_inline(tool_name, tool_registry, llm_client, state)
            if error or result is None:
                response_text = f"Error executing {tool_name}: {error or 'No result'}"
            else:
                response_text = _format_tool_result(tool_name, result)

            decision = Decision(
                action=DecisionAction.ANSWER_DIRECTLY,
                reasoning="Fast-path: self_reflect system tool (no LLM routing needed).",
                requires_planning=False,
                direct_response=response_text,
            )

            if state_manager:
                await state_manager.log_decision({
                    "event": "system_tool_executed_fast_path",
                    "tool_name": tool_name,
                    "timestamp": utcnow_isoformat(),
                })

            return {"current_decision": decision}

        # ============================================================
        # STANDARD PATH: Use LLM for routing decision
        # ============================================================

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

            action_str = decision_data.get("action", "answer_directly")

            # Get LLM's confidence score (default 0.5 if not provided)
            confidence = decision_data.get("confidence", 0.5)
            user_response = decision_data.get("user_response") or decision_data.get("direct_response")

            # Check if LLM wants a build action
            llm_action = str(action_str).lower().strip()
            is_build_action = llm_action in ("build_agent", "build_tool", "upgrade")

            # For build actions, enforce confidence threshold
            if is_build_action:
                logger.info(
                    "Build action requested: action=%s, confidence=%.2f, threshold=%.2f",
                    llm_action, confidence, build_confidence_threshold
                )

                if confidence < build_confidence_threshold:
                    # Confidence too low - fall back to answer_directly with explanation
                    logger.info(
                        "Build action rejected due to low confidence (%.2f < %.2f)",
                        confidence, build_confidence_threshold
                    )
                    decision = Decision(
                        action=DecisionAction.ANSWER_DIRECTLY,
                        reasoning=f"Build action confidence ({confidence:.2f}) below threshold ({build_confidence_threshold})",
                        requires_planning=False,
                        direct_response=(
                            f"I'm not certain you want me to build an agent. "
                            f"(Confidence: {confidence:.0%})\n\n"
                            f"If you'd like me to **build a new agent**, please say something like:\n"
                            f"- \"Build me a hello world agent\"\n"
                            f"- \"Create a new agent that [does X]\"\n\n"
                            f"Otherwise, let me know what you'd like me to do and I'll help!"
                        ),
                    )

                    if state_manager:
                        await state_manager.log_decision({
                            "event": "build_rejected_low_confidence",
                            "confidence": confidence,
                            "threshold": build_confidence_threshold,
                            "llm_action": llm_action,
                            "timestamp": utcnow_isoformat(),
                        })

                    return {"current_decision": decision}

                # Check if a build is already in progress
                if state.get("build_in_progress", False):
                    current_task = state.get("build_task_description", "unknown task")
                    phase = state.get("build_phase", "unknown")
                    decision = Decision(
                        action=DecisionAction.ANSWER_DIRECTLY,
                        reasoning="Build already in progress - rejecting new build request.",
                        requires_planning=False,
                        direct_response=(
                            f"⚠️ **Build Already In Progress**\n\n"
                            f"I'm currently working on: *{current_task}*\n"
                            f"Current phase: `{phase}`\n\n"
                            f"Please wait for the current build to complete, or ask me for `build status` to check progress."
                        ),
                    )

                    if state_manager:
                        await state_manager.log_decision({
                            "event": "build_rejected",
                            "reason": "build_in_progress",
                            "current_task": current_task,
                            "timestamp": utcnow_isoformat(),
                        })

                    return {"current_decision": decision}

                # BL-007: Generate structured BuildSettings from LLM decision
                # Pass decision_data so we can use project_name/build_type/build_description if already extracted
                build_settings = await _generate_build_settings(
                    user_input=user_input,
                    llm_client=llm_client,
                    llm_action=llm_action,
                    decision_data=decision_data,
                )
                logger.info(
                    "BUILD_SETTINGS: project=%s, type=%s, dir=%s, description=%s",
                    build_settings.project_name,
                    build_settings.build_type,
                    build_settings.project_directory,
                    build_settings.description[:80] if build_settings.description else "None",
                )

                # Confidence is high enough - start async build
                from ..runtime.background_build import get_background_runner

                runner = get_background_runner()
                task_description = f"Build {build_settings.build_type}: {build_settings.project_name}"
                target_capabilities = _infer_target_capabilities(user_input)

                if runner:
                    try:
                        # Start the build asynchronously
                        build_id, ack_message = await runner.start_build(
                            user_input=user_input,
                            task_description=task_description,
                            target_capabilities=target_capabilities,
                            initial_state=dict(state),
                            build_settings=build_settings,  # BL-007: Pass build settings
                        )

                        logger.info("Started async build %s for: %s (confidence=%.2f)", build_id, task_description, confidence)

                        # Use user_response if provided, otherwise use default ack
                        response_msg = user_response or ack_message

                        decision = Decision(
                            action=DecisionAction.ANSWER_DIRECTLY,
                            reasoning=f"Started async build {build_id} (confidence={confidence:.2f})",
                            requires_planning=False,
                            direct_response=response_msg,
                        )

                        if state_manager:
                            await state_manager.log_decision({
                                "event": "async_build_started",
                                "build_id": build_id,
                                "task_description": task_description,
                                "build_settings": build_settings.model_dump(),
                                "confidence": confidence,
                                "timestamp": utcnow_isoformat(),
                            })

                        # Mark build settings as in progress
                        build_settings.mark_in_progress()

                        return {
                            "current_decision": decision,
                            "build_in_progress": True,
                            "task": {
                                "description": task_description,
                                "target_capabilities": target_capabilities,
                            },
                            "build_started_at": utcnow_isoformat(),
                            "build_settings": build_settings.model_dump(),  # BL-007: Serialize to dict (canonical source for build_id and user_request)
                        }

                    except RuntimeError as e:
                        logger.warning("Failed to start async build: %s", e)
                        decision = Decision(
                            action=DecisionAction.ANSWER_DIRECTLY,
                            reasoning="Build already in progress",
                            requires_planning=False,
                            direct_response=(
                                f"⚠️ **Build Already In Progress**\n\n"
                                f"{str(e)}\n\n"
                                f"Ask for `build status` to check progress."
                            ),
                        )
                        return {"current_decision": decision}
                else:
                    logger.error("No background runner available; build requests must run in background")
                    decision = Decision(
                        action=DecisionAction.ANSWER_DIRECTLY,
                        reasoning="Background build runner unavailable",
                        requires_planning=False,
                        direct_response=(
                            "⚠️ **Background Build Unavailable**\n\n"
                            "I couldn't start the build because the background runner isn't available. "
                            "Please try again in a moment."
                        ),
                    )
                    return {"current_decision": decision}

            # Parse decision
            action_str = decision_data.get("action", "answer_directly")
            try:
                action = DecisionAction(action_str)
            except ValueError:
                logger.warning("Unknown action '%s', falling back to answer_directly", action_str)
                action = DecisionAction.ANSWER_DIRECTLY

            # ============================================================
            # FAST-PATH: Handle delegate_a2a without backlog
            # ============================================================
            if action == DecisionAction.DELEGATE_A2A:
                target_capabilities = decision_data.get("target_capabilities", [])

                if not target_capabilities:
                    # No capabilities - can't route
                    decision = Decision(
                        action=DecisionAction.ANSWER_DIRECTLY,
                        reasoning="delegate_a2a without target_capabilities - responding directly",
                        requires_planning=False,
                        direct_response="I wasn't able to determine which agent to use. Could you please clarify your request?",
                    )
                    return {"current_decision": decision}

                # Get loop for streaming status
                from ..runtime.loop import get_current_loop
                loop = get_current_loop()
                capability = target_capabilities[0]
                agent_config = tool_registry.find_agent_by_capability(capability)
                agent_name = agent_config.get("name", capability) if agent_config else capability

                # Stream status message if streaming is enabled
                if loop and loop._streaming_enabled:
                    await loop.stream_token(f"Calling {agent_name}...")

                # Execute agent call
                result, error = await _execute_a2a_agent_inline(
                    target_capabilities=target_capabilities,
                    user_input=user_input,
                    tool_registry=tool_registry,
                )

                if error:
                    response_text = f"Agent unavailable: {error}"
                    if loop and loop._streaming_enabled:
                        await loop.stream_token(f"\n{response_text}")
                        loop._streamed_response_content = f"Calling {agent_name}...\n{response_text}"
                        loop._response_was_streamed = True
                else:
                    # Format successful response using helper
                    assert result is not None, "result must not be None when error is None"
                    response_text = _format_a2a_response(result, agent_name)

                    if loop and loop._streaming_enabled:
                        await loop.stream_token(f"\n\n{response_text}")
                        loop._streamed_response_content = f"Calling {agent_name}...\n\n{response_text}"
                        loop._response_was_streamed = True

                decision = Decision(
                    action=DecisionAction.ANSWER_DIRECTLY,
                    reasoning=f"Fast-path: A2A call to {agent_name} (capability: {capability})",
                    requires_planning=False,
                    target_capabilities=target_capabilities,
                    direct_response=None if (loop and loop._streaming_enabled) else response_text,
                )

                if state_manager:
                    await state_manager.log_decision({
                        "event": "a2a_agent_call_fast_path",
                        "agent_name": agent_name,
                        "capability": capability,
                        "success": error is None,
                        "error": error,
                        "timestamp": utcnow_isoformat(),
                    })

                return {"current_decision": decision}

            # For direct answers, ALWAYS stream if streaming is enabled (ignore LLM's direct_response)
            direct_resp = None
            response_streamed = False

            # Check if loop has streaming enabled
            from ..runtime.loop import get_current_loop
            loop = get_current_loop()

            logger.info("Streaming check: action=%s, confidence=%.2f, loop=%s, streaming_enabled=%s",
                        action, confidence, loop is not None, loop._streaming_enabled if loop else "N/A")

            if action == DecisionAction.ANSWER_DIRECTLY and loop and loop._streaming_enabled:
                # Generate a streaming conversational response
                logger.info("STREAMING: Starting (loop id=%s)", id(loop))
                messages = _extract_history(state.get("messages", []), limit=10)
                messages.append({"role": "user", "content": user_input})

                streamed_text = ""
                token_count = 0
                try:
                    async for token in llm_client.chat_stream(
                        messages=messages,
                        temperature=0.7,
                    ):
                        token_count += 1
                        streamed_text += token
                        await loop.stream_token(token)
                    logger.info("STREAMING: Completed - %d tokens", token_count)
                except Exception as e:
                    logger.error("STREAMING: Error after %d tokens: %s", token_count, e)
                    raise

                # Store streamed content in loop for respond node to save to messages
                loop._streamed_response_content = streamed_text

                # DON'T set direct_resp here - it was already streamed
                # Setting direct_resp = None prevents respond node from duplicating
                direct_resp = None
                response_streamed = True
                loop._response_was_streamed = True
                logger.info("STREAMING: Set _response_was_streamed=True (loop id=%s)", id(loop))
            elif action == DecisionAction.ANSWER_DIRECTLY:
                # Use user_response first, fall back to direct_response
                direct_resp = user_response or decision_data.get("direct_response")

            decision = Decision(
                action=action,
                reasoning=decision_data.get("reasoning", ""),
                requires_planning=decision_data.get("requires_planning", False),
                target_capabilities=decision_data.get("target_capabilities", []),
                direct_response=direct_resp,
            )

            decision_result: dict[str, Any] = {"current_decision": decision}

            # Log decision with confidence
            if state_manager:
                await state_manager.log_decision({
                    "event": "routing_decision",
                    "action": action.value,
                    "confidence": confidence,
                    "reasoning": decision_data.get("reasoning", ""),
                    "requires_planning": decision_data.get("requires_planning", False),
                    "target_capabilities": decision_data.get("target_capabilities", []),
                    "timestamp": utcnow_isoformat(),
                })

            return cast(ProgenitorState, decision_result)

        except (RateLimitError, APIConnectionError) as e:
            # Recoverable errors - show to user with helpful message
            logger.warning("Recoverable LLM error in decide node: %s", e)

            error_message = (
                "⚠️ **Temporary Issue**\n\n"
                f"I encountered an issue connecting to the AI service: {type(e).__name__}\n\n"
                "This is usually temporary. Please try again in a moment."
            )

            decision = Decision(
                action=DecisionAction.ANSWER_DIRECTLY,
                reasoning=f"LLM API error: {e}",
                requires_planning=False,
                direct_response=error_message,
            )

            if state_manager:
                await state_manager.log_decision({
                    "event": "routing_decision_error",
                    "error_type": type(e).__name__,
                    "error_message": str(e),
                    "recoverable": True,
                    "timestamp": utcnow_isoformat(),
                })

            return {"current_decision": decision}

        except Exception as e:
            # Unexpected errors - show to user with error details
            logger.error("Unexpected error in decide node: %s", e, exc_info=True)

            error_message = (
                "❌ **Error Processing Request**\n\n"
                f"I encountered an unexpected error while processing your request:\n"
                f"```\n{type(e).__name__}: {str(e)[:200]}\n```\n\n"
                "This has been logged. You can try rephrasing your request or ask for help."
            )

            decision = Decision(
                action=DecisionAction.ANSWER_DIRECTLY,
                reasoning=f"Fallback due to error: {type(e).__name__}: {e}",
                requires_planning=False,
                direct_response=error_message,
            )

            # Log the fallback
            if state_manager:
                await state_manager.log_decision({
                    "event": "routing_decision_error",
                    "error_type": type(e).__name__,
                    "error_message": str(e),
                    "recoverable": False,
                    "timestamp": utcnow_isoformat(),
                })

            return {"current_decision": decision}

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
