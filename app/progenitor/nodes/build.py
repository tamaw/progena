"""
Build Node - Produces candidate output via Copilot CLI.

This is the core build step that generates code artifacts in the inner loop.
Extracted from graph_inner_loop.py for consistency with other node patterns.

The build node picks pending backlog items and processes them, marking each
as completed once the copilot run finishes (regardless of test results).
"""

import logging
from pathlib import Path
from typing import Any, Callable, Coroutine, cast, TYPE_CHECKING

from ..state.models import ProgenitorState
from ..state.helpers import get_actionable_items, mark_actionable_items_completed
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
        """BUILD phase - produce candidate output via Copilot CLI.

        Picks pending backlog items and runs copilot for each, marking them
        as completed once the run finishes (regardless of test results).
        """
        logger.info("BUILD: Starting build phase")

        # Get backlog and pick pending items
        backlog = list(state.get("backlog", []))
        pending_items = get_actionable_items(backlog)

        if pending_items:
            logger.info(f"BUILD: Found {len(pending_items)} pending backlog items to process")
        else:
            logger.info("BUILD: No pending backlog items, running default build")

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

        # Get copilot_prompt from work item context or spec (set by PLAN phase)
        # Priority: 1) current_work_item.context 2) backlog item context 3) spec
        copilot_prompt = None
        spec_file_path = None

        # Try current work item context first
        current_item = state.get("current_work_item")
        if current_item:
            if isinstance(current_item, dict):
                ctx = current_item.get("context", {})
            elif hasattr(current_item, "context"):
                ctx = current_item.context if isinstance(current_item.context, dict) else {}
            else:
                ctx = {}
            copilot_prompt = ctx.get("copilot_prompt")
            spec_file_path = ctx.get("spec_file_path")

        # Try backlog items if not found
        if not copilot_prompt and backlog:
            for item in backlog:
                if isinstance(item, dict):
                    ctx = item.get("context", {})
                elif hasattr(item, "context"):
                    ctx = item.context if isinstance(item.context, dict) else {}
                else:
                    ctx = {}
                if ctx.get("copilot_prompt"):
                    copilot_prompt = ctx.get("copilot_prompt")
                    spec_file_path = ctx.get("spec_file_path")
                    break

        # Fallback to spec data from state
        if not copilot_prompt:
            spec = state.get("spec", {})
            if isinstance(spec, dict):
                spec_file_path = spec.get("file_path")

        # Fallback prompt if none provided
        if not copilot_prompt:
            task_description = state.get("build_task_description", f"Build {project_name}")
            copilot_prompt = f"Build an agent according to the spec. Task: {task_description}"
            logger.warning("BUILD: No copilot_prompt from PLAN, using fallback")

        logger.info(f"BUILD: Running Copilot in {project_directory}")
        logger.info(f"BUILD: spec_file_path={spec_file_path}")
        logger.info(f"BUILD: copilot_prompt length={len(copilot_prompt)}")

        # Run Copilot CLI - resolve to absolute path (ACP requires absolute cwd)
        working_dir = Path(project_directory)
        if not working_dir.is_absolute():
            working_dir = working_dir.resolve()
            logger.info(f"BUILD: Resolved relative path to absolute: {working_dir}")
        working_dir.mkdir(parents=True, exist_ok=True)

        build_id = (state.get("build_settings") or {}).get("build_id") or "unknown"

        try:
            build_result = await copilot_cli.build(
                work_item_id=build_id,
                worktree_path=working_dir,  # Project dir (e.g. app/agents/calculator_agent)
                prompt=copilot_prompt,
            )

            logger.info(f"BUILD: Copilot completed, success={build_result.get('success')}")

            # Track iteration (do not increment here; increment happens in learn)
            iteration_state = state.get("iteration", {})
            current_iteration = iteration_state.get("current", 0) if isinstance(iteration_state, dict) else getattr(iteration_state, "current", 0)

            # Mark pending backlog items as completed (run finished, regardless of test outcome)
            updated_backlog = mark_actionable_items_completed(backlog, build_result)

            # Update build state
            result: dict[str, Any] = {
                "build_phase": "build",
                "last_build_result": build_result,
                "backlog": updated_backlog,
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
            # Still mark items as completed on error (run attempted)
            updated_backlog = mark_actionable_items_completed(backlog, {"success": False, "error": str(e)})
            return cast(ProgenitorState, {
                "last_iteration_output": {"success": False, "error": str(e)},
                "build_phase": "build",
                "backlog": updated_backlog,
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
        copilot_cmd=config.copilot.bin_path,
        model=config.copilot.model,
        default_flags=config.copilot.default_flags,
        allowed_dirs=config.copilot.acp_allowed_dirs,
        timeout=config.copilot.timeout_seconds,
    )

    return create_node(
        llm_client=deps.llm_client,
        copilot_cli=copilot_cli,
        worktrees_dir=deps.require_worktrees_dir(),
        state_manager=deps.require_state_manager(),
    )
