"""
Prompt Templates for Progenitor.

Loads prompts from data/prompts/ directory.
All prompts are stored as markdown files for easy editing.
"""

from pathlib import Path
from typing import Optional

# Default prompts directory (relative to project root)
# Path: prompts.py -> llm/ -> progenitor/ -> app/ -> project_root/
DEFAULT_PROMPTS_DIR = Path(__file__).parent.parent.parent.parent / "data" / "prompts"


class PromptTemplates:
    """Manages prompt templates for Progenitor.

    All prompts are loaded from data/prompts/*.md files.
    """

    VERSION = "1.0.0"

    def __init__(self, prompts_dir: Optional[Path] = None):
        """Initialize with optional custom prompts directory."""
        self.prompts_dir = Path(prompts_dir) if prompts_dir else DEFAULT_PROMPTS_DIR

    def _load_prompt(self, filename: str) -> str:
        """Load a prompt from file.

        Args:
            filename: Name of the prompt file (e.g., 'genesis.md')

        Returns:
            The prompt content as a string.

        Raises:
            FileNotFoundError: If the prompt file doesn't exist.
        """
        prompt_file = self.prompts_dir / filename
        if not prompt_file.exists():
            raise FileNotFoundError(
                f"Prompt file not found: {prompt_file}. "
                f"Ensure data/prompts/{filename} exists."
            )
        return prompt_file.read_text(encoding="utf-8")

    def get_genesis_prompt(self) -> str:
        """Get the system genesis prompt (loaded on startup)."""
        return self._load_prompt("genesis.md")

    def get_decide_prompt(self, available_capabilities: list[str]) -> str:
        """Get the decision routing prompt."""
        caps_list = "\n".join([f"- {cap}" for cap in available_capabilities]) if available_capabilities else "- None registered yet"
        template = self._load_prompt("decide.md")
        return template.replace("{capabilities}", caps_list)

    def get_planner_prompt(self) -> str:
        """Get the planning prompt for work item generation."""
        return self._load_prompt("planner.md")

    def get_build_prompt(self, item_type: str) -> str:
        """Get the build specification prompt."""
        template = self._load_prompt("build.md")
        return template.replace("{item_type}", item_type)

    def get_copilot_task_prompt(self, spec: dict) -> str:
        """Generate a task prompt for Copilot CLI.

        If spec contains 'spec_doc_content', it will be included as
        additional guidance for the build (set by PLAN phase).
        """
        capabilities = spec.get('capabilities', [])
        caps_str = '\n'.join(['- ' + c for c in capabilities]) if capabilities else '- None specified'

        agent_name = spec.get('name', 'unnamed')
        kind = spec.get('kind', 'unknown')

        # Load structure guidance for agents
        if kind == 'agent':
            structure_template = self._load_prompt("agent_structure.md")
            structure_guidance = structure_template.replace(
                "{agent_name}", agent_name
            ).replace(
                "{agent_class}", agent_name.title().replace('_', '')
            )
        else:
            structure_guidance = ""

        # SpecDoc: Include persistent spec artifact if available
        spec_doc_content = spec.get('spec_doc_content', '')
        if spec_doc_content:
            # Append spec_doc as additional context for Copilot
            spec_doc_section = f"""

## Build Specification (from inner loop iterations)

The following specification has evolved through planning iterations and should guide your implementation:

{spec_doc_content}

---
"""
            structure_guidance = spec_doc_section + structure_guidance

        template = self._load_prompt("copilot_task.md")
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
