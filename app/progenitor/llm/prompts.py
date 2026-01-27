"""
Prompt Templates for Progenitor.

Loads prompts from data/prompts/ directory for easy editing.
Falls back to embedded defaults if files not found.
"""

from pathlib import Path
from typing import Optional

# Default prompts directory
DEFAULT_PROMPTS_DIR = Path(__file__).parent.parent.parent.parent.parent / "data" / "prompts"


class PromptTemplates:
    """Manages prompt templates for Progenitor."""

    VERSION = "1.0.0"

    def __init__(self, prompts_dir: Optional[Path] = None):
        """Initialize with optional custom prompts directory."""
        self.prompts_dir = Path(prompts_dir) if prompts_dir else DEFAULT_PROMPTS_DIR

    def _load_prompt(self, filename: str, default: str) -> str:
        """Load a prompt from file or return default."""
        prompt_file = self.prompts_dir / filename
        if prompt_file.exists():
            return prompt_file.read_text(encoding="utf-8")
        return default

    def get_genesis_prompt(self) -> str:
        """Get the system genesis prompt (loaded on startup)."""
        return self._load_prompt("genesis.md", DEFAULT_GENESIS_PROMPT)

    def get_decide_prompt(self, available_capabilities: list[str]) -> str:
        """Get the decision routing prompt."""
        caps_list = "\n".join([f"- {cap}" for cap in available_capabilities]) if available_capabilities else "- None registered yet"
        template = self._load_prompt("decide.md", DEFAULT_DECIDE_PROMPT)
        return template.replace("{capabilities}", caps_list)

    def get_planner_prompt(self) -> str:
        """Get the planning prompt for work item generation."""
        return self._load_prompt("planner.md", DEFAULT_PLANNER_PROMPT)

    def get_build_prompt(self, item_type: str) -> str:
        """Get the build specification prompt."""
        template = self._load_prompt("build.md", DEFAULT_BUILD_PROMPT)
        return template.replace("{item_type}", item_type)

    def get_copilot_task_prompt(self, spec: dict) -> str:
        """Generate a task prompt for Copilot CLI."""
        capabilities = spec.get('capabilities', [])
        caps_str = '\n'.join(['- ' + c for c in capabilities]) if capabilities else '- None specified'

        agent_name = spec.get('name', 'unnamed')
        kind = spec.get('kind', 'unknown')

        # Load structure guidance for agents
        if kind == 'agent':
            structure_template = self._load_prompt("agent_structure.md", DEFAULT_AGENT_STRUCTURE)
            structure_guidance = structure_template.replace(
                "{agent_name}", agent_name
            ).replace(
                "{agent_class}", agent_name.title().replace('_', '')
            )
        else:
            structure_guidance = ""

        template = self._load_prompt("copilot_task.md", DEFAULT_COPILOT_TASK)
        return template.replace(
            "{agent_name}", agent_name
        ).replace(
            "{kind}", kind
        ).replace(
            "{description}", spec.get('description', 'No description')
        ).replace(
            "{capabilities}", caps_str
        ).replace(
            "{implementation_notes}", spec.get('implementation_notes', 'None provided')
        ).replace(
            "{structure_guidance}", structure_guidance
        )


# Default prompts (fallbacks if files not found)
DEFAULT_GENESIS_PROMPT = """# Progenitor Genesis Prompt v1.0

## Identity
You are Progenitor: a perpetual, conversational personal assistant and orchestration hub.

## Decide Every Turn
For each user message, choose one:
- **Answer directly**: Simple questions, conversation, or information you already know
- **Delegate via A2A**: Call an existing agent with the right capability
- **Use MCP tool**: Call an existing MCP tool
- **Build/Upgrade capability**: Create or improve an agent or MCP server
- **Schedule**: Create a time-based task

Prefer existing capability (A2A/MCP) before building.
"""

DEFAULT_DECIDE_PROMPT = """# Decision Routing Prompt

You are the routing decision maker for Progenitor. Analyze the user's input and decide the best action.

## Available Capabilities
{capabilities}

## Decision Types
- **answer_directly**: Answer simple questions without tools or agents
- **delegate_a2a**: Use an existing A2A agent
- **build_agent**: Create a new A2A agent
- **build_mcp**: Create a new MCP server/tool
- **upgrade**: Improve an existing agent or tool
- **mixed**: Multiple actions needed
- **schedule**: Create a time-based task
"""

DEFAULT_PLANNER_PROMPT = """# Planner Prompt

Generate work items to fulfill the user's request.

## Work Item Types
- **build_agent**: Create a new A2A agent
- **build_mcp**: Create a new MCP server/tool
- **a2a_call**: Call an existing A2A agent
- **mcp_call**: Call an existing MCP tool
- **direct_answer**: Respond directly
"""

DEFAULT_BUILD_PROMPT = """# Build Specification Prompt

Generate a detailed specification for building a {item_type}.

## Directory Structure for Agents (CRITICAL)
Agents MUST be created in a subdirectory under `app/agents/<agent_name>/`:
- `app/agents/<agent_name>/agent.py` - Main agent implementation (REQUIRED)
- `app/agents/<agent_name>/__init__.py` - Export the main agent class
"""

DEFAULT_AGENT_STRUCTURE = """## Directory Structure (CRITICAL)

Create the agent in `app/agents/{agent_name}/` with:
- `app/agents/{agent_name}/agent.py` - Main implementation
- `app/agents/{agent_name}/__init__.py` - Exports

WRONG: `app/agents/{agent_name}_agent.py`
CORRECT: `app/agents/{agent_name}/agent.py`
"""

DEFAULT_COPILOT_TASK = """# Copilot Build Task

## Specification
Name: {agent_name}
Kind: {kind}
Description: {description}

Capabilities:
{capabilities}

{structure_guidance}

## Requirements
- Follow Python best practices
- Include docstrings and type hints
"""
