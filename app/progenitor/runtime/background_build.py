"""
Background Build Runner.

Manages asynchronous build operations that run in the background,
allowing the user to continue interacting while builds execute.

The runner:
- Accepts build requests and starts them as background tasks
- Runs the INNER LOOP graph (observe → think → plan → build → execute → verify → learn)
- Updates build_state.json with progress
- Stores completion/error status for later retrieval
- Supports notification callbacks for user communication
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional, TYPE_CHECKING

from .time_utils import utcnow, utcnow_isoformat

if TYPE_CHECKING:
    from ..config import ProgenitorConfig
    from ..state.manager import StateManager
    from ..llm.client import LLMClient
    from ..tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class BuildStatus(str, Enum):
    """Status of a background build."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class BuildResult:
    """Result of a background build operation."""
    build_id: str
    status: BuildStatus
    task_description: str
    started_at: datetime
    completed_at: Optional[datetime] = None
    error_message: Optional[str] = None
    output_summary: Optional[str] = None
    agent_name: Optional[str] = None
    iterations: int = 0
    verification_score: float = 0.0
    success_criteria_met: list[str] = field(default_factory=list)
    success_criteria_unmet: list[str] = field(default_factory=list)


@dataclass
class BuildRequest:
    """A request to start a background build."""
    build_id: str
    user_input: str
    task_description: str
    target_capabilities: list[str]
    created_at: datetime = field(default_factory=utcnow)


class BackgroundBuildRunner:
    """Manages background build operations using the inner loop graph.

    This runner:
    - Maintains a single active build at a time
    - Runs the inner loop graph (OBSERVE → THINK → PLAN → BUILD → EXECUTE → VERIFY → LEARN)
    - Manages build_state.json independently from the main loop
    - Provides status updates for monitoring
    """

    def __init__(
        self,
        graph,  # Outer loop graph (kept for compatibility, not used for builds)
        state_manager: "StateManager",
        config: "ProgenitorConfig",
        llm_client: "LLMClient",
        worktrees_dir: Optional[Path] = None,
    ):
        self.outer_graph = graph  # Renamed to clarify it's the outer loop
        self.state_manager = state_manager
        self.config = config
        self.llm_client = llm_client
        self.worktrees_dir = worktrees_dir or Path("worktrees")

        # Inner loop graph - created lazily
        self._inner_loop_graph = None

        self._current_build: Optional[BuildRequest] = None
        self._current_task: Optional[asyncio.Task] = None
        self._build_results: dict[str, BuildResult] = {}
        self._lock = asyncio.Lock()

        # Callback for notifying when build completes
        self._completion_callback: Optional[Callable[[BuildResult], None]] = None

    def _get_inner_loop_graph(self):
        """Lazily create the inner loop graph."""
        if self._inner_loop_graph is None:
            from ..graph_inner_loop import create_inner_loop_graph
            self._inner_loop_graph = create_inner_loop_graph(
                state_manager=self.state_manager,
                llm_client=self.llm_client,
                config=self.config,
                worktrees_dir=self.worktrees_dir,
            )
            logger.info("Created inner loop graph for builds")
        return self._inner_loop_graph

    @property
    def is_building(self) -> bool:
        """Check if a build is currently in progress."""
        return self._current_build is not None and self._current_task is not None

    @property
    def current_build_id(self) -> Optional[str]:
        """Get the current build ID if one is running."""
        return self._current_build.build_id if self._current_build else None

    def get_build_status(self, build_id: Optional[str] = None) -> Optional[BuildResult]:
        """Get the status of a build.

        Args:
            build_id: Specific build ID, or None for current/latest build.

        Returns:
            BuildResult if found, None otherwise.
        """
        if build_id:
            return self._build_results.get(build_id)

        # Return current build status if running
        if self._current_build:
            return BuildResult(
                build_id=self._current_build.build_id,
                status=BuildStatus.RUNNING,
                task_description=self._current_build.task_description,
                started_at=self._current_build.created_at,
            )

        # Return most recent completed build
        if self._build_results:
            return list(self._build_results.values())[-1]

        return None

    async def start_build(
        self,
        user_input: str,
        task_description: str,
        target_capabilities: list[str],
        initial_state: dict[str, Any],
        build_settings: Optional[Any] = None,  # BL-007: BuildSettings from decide node
    ) -> tuple[str, str]:
        """Start a background build operation.

        Args:
            user_input: The original user request.
            task_description: Description of what to build.
            target_capabilities: List of capability names to build.
            initial_state: The current state to build upon.
            build_settings: Optional BuildSettings from structured LLM output.

        Returns:
            Tuple of (build_id, acknowledgment_message).

        Raises:
            RuntimeError: If a build is already in progress.
        """
        async with self._lock:
            if self.is_building:
                raise RuntimeError(
                    f"Build already in progress: {self._current_build.task_description}"
                )

            # Use build_id from settings if provided, otherwise generate
            if build_settings and hasattr(build_settings, 'build_id'):
                build_id = build_settings.build_id
            else:
                build_id = f"build_{uuid.uuid4().hex[:12]}"

            self._current_build = BuildRequest(
                build_id=build_id,
                user_input=user_input,
                task_description=task_description,
                target_capabilities=target_capabilities,
            )

            # BL-007: Store build_settings in state for later reference
            if build_settings:
                initial_state["build_settings"] = (
                    build_settings.model_dump()
                    if hasattr(build_settings, "model_dump")
                    else build_settings
                )

            # Generate acknowledgment message via LLM
            ack_message = await self._generate_acknowledgment(
                task_description=task_description,
                target_capabilities=target_capabilities,
                build_settings=build_settings,
            )

            # Start the build task
            self._current_task = asyncio.create_task(
                self._run_build(build_id, initial_state),
                name=f"build_{build_id}",
            )

            logger.info(
                "Started background build %s: %s",
                build_id,
                task_description,
            )

            return build_id, ack_message

    async def _generate_acknowledgment(
        self,
        task_description: str,
        target_capabilities: list[str],
        build_settings: Optional[Any] = None,
    ) -> str:
        """Generate an LLM acknowledgment message for the build request."""
        try:
            # Include build settings info if available
            settings_info = ""
            if build_settings and hasattr(build_settings, 'project_name'):
                settings_info = f"\nProject: {build_settings.project_name}\nType: {build_settings.build_type}\nDirectory: {build_settings.project_directory}"

            # Use LLM to generate a friendly acknowledgment
            messages = [
                {
                    "role": "system",
                    "content": (
                        "You are a helpful AI assistant. Generate a brief, friendly acknowledgment "
                        "message (2-3 sentences) confirming that you've started working on a build "
                        "task in the background. The user can continue chatting while you work. "
                        "Mention they can ask for 'build status' to check progress. "
                        "If project details are provided, include them."
                    ),
                },
                {
                    "role": "user",
                    "content": f"I've requested to build: {task_description}{settings_info}",
                },
            ]

            response = await self.llm_client.chat(
                messages=messages,
                temperature=0.7,
                max_tokens=150,
            )

            return response.strip()

        except Exception as e:
            logger.warning("Failed to generate LLM acknowledgment: %s", e)
            # Fallback to template response
            return (
                f"🚀 **Build Started**\n\n"
                f"I've started working on your request: *{task_description}*\n\n"
                f"This is running in the background, so you can continue chatting. "
                f"Ask for `build status` anytime to check progress."
            )

    async def _run_build(
        self,
        build_id: str,
        initial_state: dict[str, Any],
    ) -> None:
        """Execute the build using the inner loop graph.

        Runs: OBSERVE → THINK → PLAN → BUILD → EXECUTE → VERIFY → LEARN (repeat)

        This manages build_state.json independently from the main loop.
        No backlog is used - the inner loop runs directly.
        """
        started_at = utcnow()
        result = BuildResult(
            build_id=build_id,
            status=BuildStatus.RUNNING,
            task_description=self._current_build.task_description,
            started_at=started_at,
        )

        try:
            # Prepare state for inner loop - NO BACKLOG
            # The inner loop runs directly, not via work items
            build_state = {
                # Build identification
                "build_id": build_id,
                "build_in_progress": True,
                "build_task_description": self._current_build.task_description,
                "build_started_at": utcnow_isoformat(),

                # User input for context
                "current_input": self._current_build.user_input,
                "user_request": self._current_build.user_input,
                "target_capabilities": self._current_build.target_capabilities,

                # Build settings (from decide phase)
                "build_settings": initial_state.get("build_settings"),

                # Inner loop control
                "current_iteration": 0,
                "max_iterations": self.config.runtime.max_inner_iterations,
                "target_score": 0.8,  # Default verification target
                "verification_score": 0.0,

                # Phase tracking
                "build_phase": "starting",
                "build_messages": [],

                # Artifacts (populated during build)
                "spec_doc": initial_state.get("spec_doc"),
                "ideal_state": None,  # Set by OBSERVE
                "current_approaches": [],  # Set by THINK
                "experiment_plan": None,  # Set by PLAN
                "artefacts": [],
                "verification_reports": [],
                "lessons_learned": [],
            }

            # Save initial build state
            await self.state_manager.save_build_state(build_state)
            logger.info(f"BUILD RUNNER: Starting inner loop for build {build_id}")

            # Get the inner loop graph
            inner_graph = self._get_inner_loop_graph()

            # Run the inner loop graph
            thread_id = f"inner_loop_{build_id}"

            async for event in inner_graph.astream(
                build_state,
                config={"configurable": {"thread_id": thread_id}},
            ):
                for node_name, node_output in event.items():
                    if isinstance(node_output, dict):
                        build_state.update(node_output)

                        # Save checkpoint after each node
                        await self.state_manager.save_build_state(build_state)

                        # Track iterations (verify node marks end of iteration)
                        if node_name == "verify":
                            result.iterations = build_state.get("current_iteration", 0)
                            result.verification_score = build_state.get("verification_score", 0)

                        # Log progress
                        logger.info(
                            "BUILD %s: node=%s, iteration=%d, score=%.2f, phase=%s",
                            build_id,
                            node_name,
                            build_state.get("current_iteration", 0),
                            build_state.get("verification_score", 0),
                            build_state.get("build_phase", "unknown"),
                        )

                        # Check for errors
                        if build_state.get("build_error"):
                            raise RuntimeError(build_state["build_error"])

            # Build completed successfully
            result.status = BuildStatus.COMPLETED
            result.completed_at = utcnow()
            result.verification_score = build_state.get("verification_score", 0)
            result.agent_name = build_state.get("agent_name") or build_state.get("build_settings", {}).get("project_name")

            # Extract success criteria results
            verification_report = build_state.get("last_verification_report", {})
            if hasattr(verification_report, "model_dump"):
                verification_report = verification_report.model_dump()

            result.success_criteria_met = verification_report.get("passed_criteria", [])
            result.success_criteria_unmet = verification_report.get("gaps", [])

            # Generate summary
            if result.agent_name:
                result.output_summary = (
                    f"Successfully built agent '{result.agent_name}' "
                    f"with verification score {result.verification_score:.2f} "
                    f"after {result.iterations} iteration(s)"
                )
            else:
                result.output_summary = (
                    f"Build completed with verification score {result.verification_score:.2f} "
                    f"after {result.iterations} iteration(s)"
                )

            logger.info(
                "BUILD %s COMPLETED: %s",
                build_id,
                result.output_summary,
            )

        except asyncio.CancelledError:
            result.status = BuildStatus.CANCELLED
            result.completed_at = utcnow()
            result.error_message = "Build was cancelled"
            logger.info("Build %s was cancelled", build_id)

        except Exception as e:
            result.status = BuildStatus.FAILED
            result.completed_at = utcnow()
            result.error_message = str(e)
            logger.error("Build %s failed: %s", build_id, e, exc_info=True)

        finally:
            # Store result and clean up
            self._build_results[build_id] = result

            async with self._lock:
                self._current_build = None
                self._current_task = None

            # Update build state with completion info
            build_state = await self.state_manager.get_build_state()
            build_state.update({
                "build_in_progress": False,
                "last_build_result": {
                    "build_id": build_id,
                    "status": result.status.value,
                    "completed_at": utcnow_isoformat(),
                    "output_summary": result.output_summary,
                    "error_message": result.error_message,
                },
            })
            await self.state_manager.save_build_state(build_state)

            # Also update current_state to clear the build reference
            await self.state_manager.update_current_state({
                "current_build_request": None,
            })

            # Trigger completion callback if set
            if self._completion_callback:
                try:
                    self._completion_callback(result)
                except Exception as e:
                    logger.error("Build completion callback failed: %s", e)

    async def cancel_build(self, build_id: Optional[str] = None) -> bool:
        """Cancel a running build.

        Args:
            build_id: Specific build to cancel, or None for current build.

        Returns:
            True if build was cancelled, False if no matching build found.
        """
        async with self._lock:
            if not self._current_task:
                return False

            if build_id and self._current_build.build_id != build_id:
                return False

            self._current_task.cancel()
            return True

    def set_completion_callback(
        self,
        callback: Callable[[BuildResult], None],
    ) -> None:
        """Set a callback to be invoked when builds complete.

        This can be used to notify users of completion/errors.
        """
        self._completion_callback = callback

    def get_recent_builds(self, limit: int = 5) -> list[BuildResult]:
        """Get recent build results."""
        results = list(self._build_results.values())
        return results[-limit:]


# Module-level reference for access from nodes
_background_runner: Optional[BackgroundBuildRunner] = None


def get_background_runner() -> Optional[BackgroundBuildRunner]:
    """Get the background build runner instance."""
    return _background_runner


def set_background_runner(runner: Optional[BackgroundBuildRunner]) -> None:
    """Set the background build runner instance."""
    global _background_runner
    _background_runner = runner
