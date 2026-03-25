"""
Prompt Templates for Progenitor.

Loads prompts from prompts/ directory.
All prompts are stored as markdown files for easy editing.
"""

import json
import os
from pathlib import Path
from typing import Any

_this_file = Path(__file__).resolve()

# Find the repo root by walking up until we see a prompts/ directory.
# This works both in-repo (…/progenitor/prompts) and in the Docker image (/app/prompts).
PROJECT_ROOT: Path | None = None
for parent in (_this_file.parent, *_this_file.parents):
    if (parent / "prompts").exists():
        PROJECT_ROOT = parent
        break

DEFAULT_PROMPTS_DIR = (PROJECT_ROOT or _this_file.parent) / "prompts"


def resolve_prompts_dir(prompts_dir: Path | str | None = None) -> Path:
    """Resolve prompt directory from explicit override, env, and project default.

    Resolution order:
    1. Explicit ``prompts_dir`` argument (always honored)
    2. ``PROGENITOR_PROMPTS_DIR`` environment variable (raises if missing)
    3. Project default ``<root>/prompts`` (raises if missing)
    """
    if prompts_dir is not None:
        return Path(prompts_dir).expanduser().resolve()

    env_prompts_dir = os.getenv("PROGENITOR_PROMPTS_DIR", "").strip()
    if env_prompts_dir:
        resolved_env_dir = Path(env_prompts_dir).expanduser().resolve()
        if not resolved_env_dir.exists():
            raise FileNotFoundError(
                f"PROGENITOR_PROMPTS_DIR points to missing directory: {resolved_env_dir}"
            )
        return resolved_env_dir

    if not DEFAULT_PROMPTS_DIR.exists():
        raise FileNotFoundError(
            f"Prompts directory not found: {DEFAULT_PROMPTS_DIR}. "
            "Ensure the prompts/ directory exists at the project root."
        )
    return DEFAULT_PROMPTS_DIR


class PromptTemplates:
    """Manages prompt templates for Progenitor.

    All prompts are loaded from prompts/*.md files.
    """

    VERSION = "1.0.0"

    def __init__(self, prompts_dir: Path | str | None = None):
        """Initialize with optional custom prompts directory."""
        self.prompts_dir = resolve_prompts_dir(prompts_dir)

    @staticmethod
    def _strip_frontmatter(content: str) -> str:
        """Remove SKILL.md frontmatter when present."""
        if not content.startswith("---"):
            return content.strip()
        parts = content.split("---", 2)
        if len(parts) < 3:
            return content.strip()
        return parts[2].strip()

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
            searched_dirs = [str(self.prompts_dir)]
            if self.prompts_dir != DEFAULT_PROMPTS_DIR:
                searched_dirs.append(str(DEFAULT_PROMPTS_DIR))
            raise FileNotFoundError(
                f"Prompt file not found: {prompt_file}. "
                f"Ensure prompts/{filename} exists. Searched prompt directories: {', '.join(searched_dirs)}"
            )
        return prompt_file.read_text(encoding="utf-8")

    def get(self, filename: str, **replacements: str) -> str:
        """Load a prompt template and apply optional replacements.

        Generic loader for any prompt file in prompts/.
        Template variables use {placeholder} syntax.

        Args:
            filename: Name of the prompt file (e.g., 'act.upgrade.md')
            **replacements: Key-value pairs for template variable substitution.
                           Keys should match {placeholder} names in the template.

        Returns:
            The prompt content with replacements applied.
        """
        content = self._load_prompt(filename)
        for key, value in replacements.items():
            content = content.replace(f"{{{key}}}", str(value))
        return content

    def get_genesis_prompt(self) -> str:
        """Get the system genesis prompt (loaded on startup)."""
        return self._load_prompt("progenitor/genesis.md")

    def get_decide_prompt(self, tool_context: str) -> str:
        """Get the decision routing prompt with full tool/agent context."""
        template = self._load_prompt("progenitor/decide.md")
        return template.replace("{tool_context}", tool_context)

    def get_planner_prompt(self) -> str:
        """Get the planning prompt for work item generation."""
        prompt = self._load_prompt("builder_agent/planner.md")
        skill_context = self.get_builder_skill_context()
        if not skill_context:
            return prompt
        return f"{prompt}\n\n## Repository Skills\n\n{skill_context}"

    def get_build_prompt(self, item_type: str) -> str:
        """Get the build specification prompt."""
        template = self._load_prompt("builder_agent/build.md")
        return template.replace("{item_type}", item_type)

    def get_builder_skill_context(self, skill_names: list[str] | None = None) -> str:
        """Load repo-level builder skills for prompt grounding.

        The builder aligns to the same `prompts/skills` convention as Progenitor
        rather than maintaining a private skills directory.
        """
        selected = skill_names or ["build-agent", "git-workflow"]
        skills_dir = self.prompts_dir / "skills"
        if not skills_dir.is_dir():
            raise FileNotFoundError(
                f"Skills directory not found: {skills_dir}. "
                "Ensure prompts/skills is mounted and populated."
            )

        sections: list[str] = []
        for skill_name in selected:
            skill_file = skills_dir / skill_name / "SKILL.md"
            if not skill_file.is_file():
                raise FileNotFoundError(
                    f"Skill file not found: {skill_file}. "
                    f"Ensure prompts/skills/{skill_name}/SKILL.md exists."
                )
            body = self._strip_frontmatter(skill_file.read_text(encoding="utf-8"))
            if body:
                sections.append(body)
        return "\n\n".join(sections)

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
            structure_template = self._load_prompt("builder_agent/agent_structure.md")
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

        template = self._load_prompt("builder_agent/copilot_task.md")
        prompt = template.replace(
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
        skill_context = self.get_builder_skill_context()
        if not skill_context:
            return prompt
        return f"{prompt}\n\n## Repository Skills\n\n{skill_context}"

    # ================================================================
    # Ideal State prompts
    # ================================================================

    def get_ideal_state_extraction_prompt(
        self,
        task: str,
        context: dict[str, Any],
    ) -> str:
        """Get prompt for extracting ideal state from a task description."""
        template = self._load_prompt("builder_agent/ideal_state_extract.md")
        context_str = json.dumps(context, indent=2, default=str) if context else "{}"
        return template.replace("{task}", task).replace("{context}", context_str)

    def get_ideal_state_refinement_prompt(
        self,
        current_state_json: str,
        results_str: str,
    ) -> str:
        """Get prompt for refining ideal state after an iteration."""
        template = self._load_prompt("builder_agent/ideal_state_refine.md")
        return (
            template
            .replace("{current_state}", current_state_json)
            .replace("{results}", results_str)
        )

    def get_ideal_state_clarification_prompt(
        self,
        original_task: str,
        trigger: str,
        clarification: str,
        current_state_json: str,
    ) -> str:
        """Get prompt for incorporating user clarification."""
        template = self._load_prompt("builder_agent/ideal_state_clarify.md")
        return (
            template
            .replace("{original_task}", original_task)
            .replace("{trigger}", trigger)
            .replace("{clarification}", clarification)
            .replace("{current_state}", current_state_json)
        )

    def get_ideal_state_anti_criteria_prompt(
        self,
        violations_str: str,
        existing_anti_criteria_str: str,
        task_description: str,
    ) -> str:
        """Get prompt for extracting anti-criteria from violations."""
        template = self._load_prompt("builder_agent/ideal_state_anti_criteria.md")
        return (
            template
            .replace("{violations}", violations_str)
            .replace("{existing_anti_criteria}", existing_anti_criteria_str)
            .replace("{task_description}", task_description)
        )

    def get_ideal_state_validation_prompt(
        self,
        criteria_str: str,
        anti_criteria_str: str,
        task_description: str,
    ) -> str:
        """Get prompt for validating criteria quality."""
        template = self._load_prompt("builder_agent/ideal_state_validate.md")
        return (
            template
            .replace("{criteria}", criteria_str)
            .replace("{anti_criteria}", anti_criteria_str)
            .replace("{task_description}", task_description)
        )

    def get_ideal_state_verification_prompt(
        self,
        criteria_str: str,
        task_description: str,
        context_str: str,
    ) -> str:
        """Get prompt for suggesting verification methods."""
        template = self._load_prompt("builder_agent/ideal_state_verify.md")
        return (
            template
            .replace("{criteria}", criteria_str)
            .replace("{task_description}", task_description)
            .replace("{context}", context_str)
        )

    # ================================================================
    # Think phase prompts
    # ================================================================

    def get_think_prompt(
        self,
        task_description: str,
        criteria: str,
        anti_criteria: str,
        iteration_context: str,
        lessons_context: str,
        focus_items: str,
        tools_context: str = "",
        constraints_context: str = "",
        dependencies_context: str = "",
    ) -> str:
        """Get prompt for the THINK phase approach generation.

        Args:
            task_description: The ideal state task description.
            criteria: Formatted success criteria text.
            anti_criteria: Formatted anti-criteria text.
            iteration_context: Previous iteration context (empty string if first iteration).
            lessons_context: Lessons from previous iterations (empty string if none).
            focus_items: Dynamic focus items based on current state.
            tools_context: Available tools context (empty string if none).
            constraints_context: Resource constraints (empty string if none).
            dependencies_context: Dependencies information (empty string if none).
        """
        template = self._load_prompt("builder_agent/think.md")
        return (
            template
            .replace("{task_description}", task_description)
            .replace("{criteria}", criteria)
            .replace("{anti_criteria}", anti_criteria)
            .replace("{iteration_context}", iteration_context)
            .replace("{lessons_context}", lessons_context)
            .replace("{focus_items}", focus_items)
            .replace("{tools_context}", tools_context)
            .replace("{constraints_context}", constraints_context)
            .replace("{dependencies_context}", dependencies_context)
        )

    # ================================================================
    # Learn phase prompts
    # ================================================================

    def get_learn_prompt(
        self,
        iteration: int,
        user_request: str,
        ideal_state: str,
        experiment_plan: str,
        think_phase: str,
        execution_result: str,
        verification_report: str,
        previous_lessons: str,
        best_result: str,
    ) -> str:
        """Get prompt for the LEARN phase iteration analysis."""
        template = self._load_prompt("builder_agent/learn.md")
        return (
            template
            .replace("{iteration}", str(iteration))
            .replace("{user_request}", user_request)
            .replace("{ideal_state}", ideal_state)
            .replace("{experiment_plan}", experiment_plan)
            .replace("{think_phase}", think_phase)
            .replace("{execution_result}", execution_result)
            .replace("{verification_report}", verification_report)
            .replace("{previous_lessons}", previous_lessons)
            .replace("{best_result}", best_result)
        )

    # ================================================================
    # Validation prompts
    # ================================================================

    def get_validation_severity_prompt(
        self,
        findings_json: str,
        project_directory: str,
        iteration: int,
    ) -> str:
        """Get prompt for LLM-based severity assessment of validation findings."""
        template = self._load_prompt("builder_agent/validation_severity.md")
        return (
            template
            .replace("{findings_json}", findings_json)
            .replace("{project_directory}", project_directory)
            .replace("{iteration}", str(iteration))
        )
