"""
Inner Loop Graph for Build Operations.

Implements the build loop per loop.spec.md:
OBSERVE → THINK → PLAN → BUILD → EXECUTE → VERIFY → LEARN → (repeat until done)

This graph runs asynchronously from the main loop, managing build_state.json.
The outer loop continues to handle user interactions while builds run in background.
"""

import logging
from typing import Literal, Any, Optional, cast
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

from .state.models import ProgenitorState

logger = logging.getLogger(__name__)


def should_continue_inner_loop(state: ProgenitorState) -> Literal["observe", "done"]:
    """Determine if inner loop should continue or exit.

    Exit conditions (per spec):
    - Minimum verification score reached
    - Max iterations hit
    - Build cancelled/error
    """
    # Check for cancellation
    if state.get("build_cancelled", False):
        logger.info("Build cancelled, exiting inner loop")
        return "done"

    # Check for error
    if state.get("build_error"):
        logger.info("Build error, exiting inner loop")
        return "done"

    # Check iteration limit
    current_iteration = state.get("current_iteration", 0)
    max_iterations = state.get("max_iterations", 10)

    if current_iteration >= max_iterations:
        logger.info("Max iterations (%d) reached, exiting inner loop", max_iterations)
        return "done"

    # Check verification score
    verification_score = state.get("verification_score", 0.0)
    target_score = state.get("target_score", 0.8)

    if verification_score >= target_score:
        logger.info(
            "Verification score %.2f meets target %.2f, exiting inner loop",
            verification_score, target_score
        )
        return "done"

    # Continue iterating
    logger.info(
        "Continuing inner loop: iteration=%d/%d, score=%.2f/%.2f",
        current_iteration, max_iterations, verification_score, target_score
    )
    return "observe"


def route_after_verify(state: ProgenitorState) -> Literal["learn", "done"]:
    """Route after verification - learn if continuing, done if complete."""
    # Check if we should stop
    verification_score = state.get("verification_score", 0.0)
    target_score = state.get("target_score", 0.8)
    current_iteration = state.get("current_iteration", 0)
    max_iterations = state.get("max_iterations", 10)

    # Stop conditions
    if state.get("build_cancelled", False):
        return "done"
    if state.get("build_error"):
        return "done"
    if verification_score >= target_score:
        return "done"
    if current_iteration >= max_iterations:
        return "done"

    # Continue to learn phase
    return "learn"


def create_inner_loop_graph(
    state_manager,
    llm_client,
    config,
    worktrees_dir,
):
    """Create the inner loop graph for build operations.

    Args:
        state_manager: StateManager for build_state.json persistence.
        llm_client: LLM client for model calls.
        config: ProgenitorConfig with build settings.
        worktrees_dir: Path to worktrees directory.

    Returns:
        Compiled StateGraph implementing:
        OBSERVE → THINK → PLAN → BUILD → EXECUTE → VERIFY → LEARN → (loop)
    """
    from .nodes import (
        observe,
        think,
        planner,
        execute,
        learn,
        verify_loop,
    )
    from .tools.copilot import CopilotCLI
    from .tools.registry import ToolRegistry
    from pathlib import Path

    # Initialize tools needed for build
    copilot_cli = CopilotCLI(
        config_path=config.copilot.config_path,
        timeout=config.copilot.timeout_seconds,
    )

    # Tool registry for execute phase
    registry_path = config.storage.state_dir / "registry.json"
    tool_registry = ToolRegistry(registry_path)

    # Create node functions for inner loop
    nodes = {
        "observe": observe.create_node(llm_client, state_manager),
        "think": think.create_node(llm_client, state_manager),
        "plan": planner.create_node(llm_client, state_manager),
        "build": _create_build_node(llm_client, copilot_cli, worktrees_dir, state_manager),
        "execute": execute.create_node(tool_registry, state_manager, worktrees_dir),
        "verify": verify_loop.create_node(llm_client, state_manager),
        "learn": learn.create_node(llm_client, state_manager),
    }

    # Build the graph
    builder = StateGraph(ProgenitorState)

    # Add all nodes
    for name, node_fn in nodes.items():
        builder.add_node(name, node_fn)

    # Set entry point
    builder.set_entry_point("observe")

    # Define edges for the inner loop
    # OBSERVE → THINK → PLAN → BUILD → EXECUTE → VERIFY → [LEARN or DONE]
    builder.add_edge("observe", "think")
    builder.add_edge("think", "plan")
    builder.add_edge("plan", "build")
    builder.add_edge("build", "execute")
    builder.add_edge("execute", "verify")

    # After verify, either learn (continue) or done (exit)
    builder.add_conditional_edges(
        "verify",
        route_after_verify,
        {
            "learn": "learn",
            "done": END,
        }
    )

    # After learn, check if we should continue
    builder.add_conditional_edges(
        "learn",
        should_continue_inner_loop,
        {
            "observe": "observe",
            "done": END,
        }
    )

    # Compile with checkpointing
    memory = MemorySaver()
    graph = builder.compile(checkpointer=memory)

    logger.info("Inner loop graph created with nodes: %s", list(nodes.keys()))
    return graph


def _create_build_node(llm_client, copilot_cli, worktrees_dir, state_manager):
    """Create the BUILD node that runs Copilot CLI.

    This is the core build step that generates code artifacts.
    """
    from pathlib import Path
    from .runtime.time_utils import utcnow_isoformat

    async def build_node(state: ProgenitorState) -> ProgenitorState:
        """BUILD phase - produce candidate output via Copilot CLI."""
        logger.info("BUILD: Starting build phase")

        # Get build settings
        build_settings = state.get("build_settings")
        if not build_settings:
            logger.error("BUILD: No build_settings in state")
            return cast(ProgenitorState, {"last_iteration_output": {"success": False, "error": "No build_settings provided"}})

        # Get project directory
        if isinstance(build_settings, dict):
            project_directory = build_settings.get("project_directory")
            project_name = build_settings.get("project_name", "agent")
        else:
            project_directory = getattr(build_settings, "project_directory", None)
            project_name = getattr(build_settings, "project_name", "agent")

        if not project_directory:
            logger.error("BUILD: No project_directory in build_settings")
            return cast(ProgenitorState, {"last_iteration_output": {"success": False, "error": "No project_directory in build_settings"}})

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

        build_id = state.get("build_id", "unknown")

        try:
            build_result = await copilot_cli.build(
                work_item_id=build_id,
                worktree_path=working_dir.parent,  # Parent of project dir
                prompt=copilot_prompt,
            )

            logger.info(f"BUILD: Copilot completed, success={build_result.get('success')}")

            # Track iteration
            current_iteration = state.get("current_iteration", 0) + 1

            # Update build state
            result = {
                "current_iteration": current_iteration,
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
