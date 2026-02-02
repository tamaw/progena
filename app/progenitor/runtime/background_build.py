"""
Background Build Runner.

Manages asynchronous build operations that run in the background,
allowing the user to continue interacting while builds execute.

The runner:
- Accepts build requests and starts them as background tasks
- Updates state with progress
- Stores completion/error status for later retrieval
- Supports future notification callbacks
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
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
    """Manages background build operations.

    This runner maintains a single active build at a time and
    provides status updates for monitoring.
    """

    def __init__(
        self,
        graph,
        state_manager: "StateManager",
        config: "ProgenitorConfig",
        llm_client: "LLMClient",
    ):
        self.graph = graph
        self.state_manager = state_manager
        self.config = config
        self.llm_client = llm_client

        self._current_build: Optional[BuildRequest] = None
        self._current_task: Optional[asyncio.Task] = None
        self._build_results: dict[str, BuildResult] = {}
        self._lock = asyncio.Lock()

        # Callback for notifying when build completes (for future use)
        self._completion_callback: Optional[Callable[[BuildResult], None]] = None

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
    ) -> tuple[str, str]:
        """Start a background build operation.

        Args:
            user_input: The original user request.
            task_description: Description of what to build.
            target_capabilities: List of capability names to build.
            initial_state: The current state to build upon.

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

            build_id = f"build_{uuid.uuid4().hex[:12]}"

            self._current_build = BuildRequest(
                build_id=build_id,
                user_input=user_input,
                task_description=task_description,
                target_capabilities=target_capabilities,
            )

            # Generate acknowledgment message via LLM
            ack_message = await self._generate_acknowledgment(
                task_description=task_description,
                target_capabilities=target_capabilities,
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
    ) -> str:
        """Generate an LLM acknowledgment message for the build request."""
        try:
            # Use LLM to generate a friendly acknowledgment
            messages = [
                {
                    "role": "system",
                    "content": (
                        "You are a helpful AI assistant. Generate a brief, friendly acknowledgment "
                        "message (2-3 sentences) confirming that you've started working on a build "
                        "task in the background. The user can continue chatting while you work. "
                        "Mention they can ask for 'build status' to check progress."
                    ),
                },
                {
                    "role": "user",
                    "content": f"I've requested to build: {task_description}",
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
        """Execute the build in the background.

        This runs the graph with build-specific state and tracks progress.
        """
        started_at = utcnow()
        result = BuildResult(
            build_id=build_id,
            status=BuildStatus.RUNNING,
            task_description=self._current_build.task_description,
            started_at=started_at,
        )

        try:
            # Prepare state for build
            build_state = dict(initial_state)
            build_state["build_in_progress"] = True
            build_state["build_task_description"] = self._current_build.task_description
            build_state["build_started_at"] = utcnow_isoformat()
            build_state["build_id"] = build_id
            build_state["current_input"] = self._current_build.user_input

            # Create work item for the build
            from ..state.models import WorkItem, WorkItemType, WorkItemStatus

            work_item = WorkItem(
                id=str(uuid.uuid4()),
                item_type=WorkItemType.BUILD_AGENT,
                status=WorkItemStatus.PENDING,
                description=self._current_build.task_description,
                context={
                    "user_request": self._current_build.user_input,
                    "target_capabilities": self._current_build.target_capabilities,
                    "build_id": build_id,
                },
            )

            build_state["backlog"] = [work_item]

            # Run the graph
            thread_id = f"build_{build_id}"
            iteration_count = 0

            async for event in self.graph.astream(
                build_state,
                config={"configurable": {"thread_id": thread_id}},
            ):
                for node_name, node_output in event.items():
                    if isinstance(node_output, dict):
                        build_state.update(node_output)

                        # Track iterations
                        if node_name == "verify":
                            iteration_count += 1
                            result.iterations = iteration_count
                            result.verification_score = build_state.get("verification_score", 0)

                        # Log progress
                        logger.debug(
                            "Build %s: node=%s, iteration=%d, score=%.2f",
                            build_id,
                            node_name,
                            iteration_count,
                            result.verification_score,
                        )

            # Build completed successfully
            result.status = BuildStatus.COMPLETED
            result.completed_at = utcnow()
            result.verification_score = build_state.get("verification_score", 0)
            result.agent_name = build_state.get("new_agent_ready", {}).get("name")

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
                    f"with verification score {result.verification_score:.2f}"
                )
            else:
                result.output_summary = (
                    f"Build completed with verification score {result.verification_score:.2f}"
                )

            logger.info(
                "Build %s completed: %s (score=%.2f, iterations=%d)",
                build_id,
                result.output_summary,
                result.verification_score,
                result.iterations,
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

            # Update state manager
            await self.state_manager.update_state({
                "build_in_progress": False,
                "last_build_result": {
                    "build_id": build_id,
                    "status": result.status.value,
                    "completed_at": utcnow_isoformat(),
                    "output_summary": result.output_summary,
                    "error_message": result.error_message,
                },
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
