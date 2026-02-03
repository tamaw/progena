"""
Build Node - Produces candidate output via Copilot CLI.

This is the core build step that generates code artifacts in the inner loop.
Extracted from graph_inner_loop.py for consistency with other node patterns.
"""

import logging
from pathlib import Path
from typing import Any, Callable, Coroutine, Optional, cast, TYPE_CHECKING

from ..state.models import ProgenitorState
from ..runtime.time_utils import utcnow_isoformat

if TYPE_CHECKING:
    from ..tools.copilot import CopilotCLI
    from ..dependencies import NodeDependencies

logger = logging.getLogger(__name__)


def create_node(
    llm_client,
    copilot_cli: "CopilotCLI",
    worktrees_dir: Path,
    state_manager,
) -> Callable[[ProgenitorState], Coroutine[Any, Any, ProgenitorState]]:
    """Create the BUILD node that runs Copilot CLI.

    This is the core build step that generates code artifacts.

    Args:
        llm_client: LLM client for model calls (currently unused but for future).
        copilot_cli: CopilotCLI instance for running builds.
        worktrees_dir: Path to worktrees directory.
        state_manager: State manager for saving build state.

    Returns:
        Async node function for LangGraph.
    """

    async def build_node(state: ProgenitorState) -> ProgenitorState:
        """BUILD phase - produce candidate output via Copilot CLI."""
        logger.info("BUILD: Starting build phase")

        # Get build settings
        build_settings = state.get("build_settings")
        if not build_settings:
            logger.error("BUILD: No build_settings in state")
            return cast(ProgenitorState, {
                "last_iteration_output": {
                    "success": False,
                    "error": "No build_settings provided"
                }
            })

        # Get project directory
        if isinstance(build_settings, dict):
            project_directory = build_settings.get("project_directory")
            project_name = build_settings.get("project_name", "agent")
        else:
            project_directory = getattr(build_settings, "project_directory", None)
            project_name = getattr(build_settings, "project_name", "agent")

        if not project_directory:
            logger.error("BUILD: No project_directory in build_settings")
            return cast(ProgenitorState, {
                "last_iteration_output": {
                    "success": False,
                    "error": "No project_directory in build_settings"
                }
            })

        # Get spec_doc and copilot_prompt from state (set by PLAN phase)
        spec_doc = state.get("spec_doc")
        copilot_prompt = None
        spec_file_path = None

        if spec_doc:
            if isinstance(spec_doc, dict):
                copilot_prompt = spec_doc.get("copilot_prompt")
                spec_file_path = spec_doc.get("spec_file_path")
            else:
                copilot_prompt = getattr(spec_doc, "copilot_prompt", None)
                spec_file_path = getattr(spec_doc, "spec_file_path", None)

        # Fallback prompt if none provided
        if not copilot_prompt:
            task_description = state.get("build_task_description", f"Build {project_name}")
            copilot_prompt = f"Build an agent according to the spec. Task: {task_description}"
            logger.warning("BUILD: No copilot_prompt from PLAN, using fallback")

        logger.info(f"BUILD: Running Copilot in {project_directory}")
        logger.info(f"BUILD: spec_file_path={spec_file_path}")
        logger.info(f"BUILD: copilot_prompt length={len(copilot_prompt)}")

        # Run Copilot CLI
        working_dir = Path(project_directory)
        working_dir.mkdir(parents=True, exist_ok=True)

        build_id = state.get("build_id") or "unknown"

        try:
            build_result = await copilot_cli.build(
                work_item_id=build_id,
                worktree_path=working_dir.parent,  # Parent of project dir
                prompt=copilot_prompt,
            )

            logger.info(f"BUILD: Copilot completed, success={build_result.get('success')}")

            # Track iteration (do not increment here; increment happens in learn)
            current_iteration = state.get("current_iteration", 0)

            # Update build state
            result: dict[str, Any] = {
                "build_phase": "build",
                "last_build_result": build_result,
                "build_messages": state.get("build_messages", []) + [
                    {
                        "phase": "build",
                        "iteration": current_iteration,
                        "timestamp": utcnow_isoformat(),
                        "success": build_result.get("success", False),
                    }
                ],
            }

            if not build_result.get("success"):
                result["build_error"] = build_result.get("error") or build_result.get("stderr")

            # Save checkpoint to build_state.json
            await state_manager.save_build_state({
                **state,
                **result,
            })

            return cast(ProgenitorState, result)

        except Exception as e:
            logger.error(f"BUILD: Exception during build: {e}", exc_info=True)
            return cast(ProgenitorState, {
                "last_iteration_output": {"success": False, "error": str(e)},
                "build_phase": "build",
            })

    return build_node


def create_node_from_deps(deps: "NodeDependencies") -> Callable[[ProgenitorState], Coroutine[Any, Any, ProgenitorState]]:
    """Create build node from NodeDependencies container.

    This is the preferred factory method using the new dependency injection pattern.

    Args:
        deps: NodeDependencies container with all required dependencies.

    Returns:
        Async node function for LangGraph.
    """
    from ..tools.copilot import CopilotCLI

    config = deps.require_config()

    copilot_cli = CopilotCLI(
        config_path=config.copilot.config_path,
        timeout=config.copilot.timeout_seconds,
    )

    return create_node(
        llm_client=deps.llm_client,
        copilot_cli=copilot_cli,
        worktrees_dir=deps.require_worktrees_dir(),
        state_manager=deps.require_state_manager(),
    )
