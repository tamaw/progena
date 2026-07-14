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
import contextlib
import logging
import os
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from .hooks import HookContext, create_default_hook_manager
from .time_utils import utcnow, utcnow_isoformat


def deep_merge(base: dict, updates: dict) -> dict:
    """Deep merge updates into base dictionary.

    Recursively merges nested dicts instead of replacing them.
    For non-dict values, updates override base.

    Args:
        base: The base dictionary to merge into.
        updates: The updates to apply.

    Returns:
        The merged dictionary (base is modified in place and returned).
    """
    for key, value in updates.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            # Recursively merge nested dicts
            deep_merge(base[key], value)
        else:
            # Override with new value
            base[key] = value
    return base


async def _run_git(
    args: list[str],
    *,
    cwd: Path,
    timeout_seconds: float = 60.0,
) -> dict[str, Any]:
    """Run a git command and return {returncode, stdout, stderr}."""
    process = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
    return {
        "returncode": process.returncode,
        "stdout": (stdout or b"").decode(errors="replace"),
        "stderr": (stderr or b"").decode(errors="replace"),
    }


async def _prepare_integration_branch(
    *,
    repo_path: Path,
    integration_branch: str,
    base_branch: str = "main",
) -> str:
    """Create/reset an integration branch from base and check it out.

    If origin/{base_branch} exists, prefer it as the base ref.
    Returns the base ref used (e.g., origin/main or main).
    """
    # Try to update origin refs, but don't hard-fail (repo may not have a remote configured).
    await _run_git(["fetch", "origin", base_branch], cwd=repo_path, timeout_seconds=60.0)

    base_ref = base_branch
    has_origin = await _run_git(
        ["show-ref", "--verify", f"refs/remotes/origin/{base_branch}"],
        cwd=repo_path,
        timeout_seconds=10.0,
    )
    if has_origin["returncode"] == 0:
        base_ref = f"origin/{base_branch}"

    # Create/reset integration branch and check it out.
    result = await _run_git(
        ["checkout", "-B", integration_branch, base_ref], cwd=repo_path, timeout_seconds=30.0
    )
    if result["returncode"] != 0:
        raise RuntimeError(
            f"Failed to checkout integration branch {integration_branch}: {result['stderr']}".strip()
        )

    return base_ref


async def _cleanup_worktrees(*, repo_path: Path, worktrees: list[str]) -> None:
    """Best-effort removal of git worktrees recorded during the build."""
    for wt in worktrees:
        try:
            await _run_git(
                ["worktree", "remove", "--force", wt], cwd=repo_path, timeout_seconds=60.0
            )
        except (OSError, TimeoutError) as exc:
            logger.debug("Failed to remove worktree %s: %s", wt, exc)


def _expected_output_path(repo_path: Path, build_state: dict[str, Any]) -> Path | None:
    """Resolve the expected output path for builds that specify project_directory."""
    build_settings = build_state.get("build_settings")
    if not isinstance(build_settings, dict):
        return None
    project_directory = build_settings.get("project_directory")
    if not isinstance(project_directory, str) or not project_directory.strip():
        return None
    project_path = Path(project_directory.strip())
    if project_path.is_absolute():
        return project_path
    return repo_path / project_path


def _expected_output_path_candidates(repo_path: Path, build_state: dict[str, Any]) -> list[Path]:
    """Return candidate expected output paths for relative project_directory values."""
    expected = _expected_output_path(repo_path, build_state)
    if expected is None:
        return []
    if expected.is_absolute() and str(expected) == str(Path(expected)):
        # For absolute paths, there is only one valid candidate.
        build_settings = build_state.get("build_settings")
        project_directory = (
            build_settings.get("project_directory") if isinstance(build_settings, dict) else None
        )
        if isinstance(project_directory, str) and Path(project_directory.strip()).is_absolute():
            return [expected]

    build_settings = build_state.get("build_settings")
    project_directory = (
        build_settings.get("project_directory") if isinstance(build_settings, dict) else None
    )
    if not isinstance(project_directory, str):
        return [expected]
    project_path = Path(project_directory.strip())
    if project_path.is_absolute():
        return [project_path]

    candidate_bases: list[Path] = [repo_path]
    if repo_path.parent != repo_path:
        candidate_bases.append(repo_path.parent)
    project_root_env = os.getenv("PROGENITOR_PROJECT_ROOT")
    if project_root_env:
        candidate_bases.append(Path(project_root_env))
    candidate_bases.append(Path.cwd())

    seen: set[str] = set()
    candidates: list[Path] = []
    for base in candidate_bases:
        candidate = (base / project_path).resolve()
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        candidates.append(candidate)
    return candidates


def _assert_expected_output_exists(repo_path: Path, build_state: dict[str, Any]) -> None:
    """Fail fast if a build reports success but expected filesystem output is missing."""
    candidates = _expected_output_path_candidates(repo_path, build_state)
    if not candidates:
        return
    for expected_path in candidates:
        if not expected_path.exists():
            continue
        if expected_path.is_dir() and not any(p.is_file() for p in expected_path.rglob("*")):
            raise RuntimeError(f"Build output directory is empty: {expected_path}")
        return
    raise RuntimeError(f"Build completed without creating expected output path: {candidates[0]}")


def _extract_published_pr_url(build_state: dict[str, Any]) -> str | None:
    build_messages = build_state.get("build_messages")
    if isinstance(build_messages, list):
        for msg in reversed(build_messages):
            pr_url = msg.get("pr_url") if isinstance(msg, dict) else None
            if (
                isinstance(msg, dict)
                and msg.get("phase") == "publish"
                and msg.get("pr_status") == "created"
                and isinstance(pr_url, str)
                and pr_url.strip()
            ):
                return pr_url.strip()

    current_work_item = build_state.get("current_work_item")
    if isinstance(current_work_item, dict):
        context = current_work_item.get("context")
        if isinstance(context, dict):
            pr_url = context.get("pr_url")
            if isinstance(pr_url, str) and pr_url.strip():
                return pr_url.strip()
    return None


def _assert_pr_created_if_required(build_state: dict[str, Any]) -> str | None:
    build_settings = build_state.get("build_settings")
    build_type = ""
    if isinstance(build_settings, dict):
        build_type = str(build_settings.get("build_type") or "").lower().strip()

    if build_type not in {"agent", "mcp", "progenitor", "upgrade"}:
        return None

    pr_url = _extract_published_pr_url(build_state)
    if not pr_url:
        raise RuntimeError("Build completed without creating a GitHub PR")
    return pr_url


def _resolve_repo_path(worktrees_dir: Path) -> Path:
    """Resolve the git repo root for builds in local and container environments."""
    repo_root_env = os.getenv("PROGENITOR_PROJECT_ROOT")
    if repo_root_env:
        return Path(repo_root_env)
    candidate = Path(__file__).resolve().parents[4]
    if (candidate / ".git").exists():
        return candidate
    return worktrees_dir.parent


if TYPE_CHECKING:
    from ..config import ProgenitorConfig
    from ..llm.client import LLMClient
    from ..state.manager import StateManager

logger = logging.getLogger(__name__)


class BuildStatus(StrEnum):
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
    completed_at: datetime | None = None
    error_message: str | None = None
    output_summary: str | None = None
    agent_name: str | None = None
    iterations: int = 0
    verification_score: float = 0.0
    success_criteria_met: list[str] = field(default_factory=list)
    success_criteria_unmet: list[str] = field(default_factory=list)
    pr_url: str | None = None


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
        state_manager: "StateManager",
        config: "ProgenitorConfig",
        llm_client: "LLMClient",
        worktrees_dir: Path | None = None,
        copilot_acp_manager=None,
        hook_manager=None,
    ):
        self.state_manager = state_manager
        self.config = config
        self.llm_client = llm_client
        self.worktrees_dir = worktrees_dir or config.storage.worktree_dir
        self.copilot_acp_manager = copilot_acp_manager
        hook_registrations = getattr(getattr(config, "runtime", None), "hook_registrations", None)
        self.hook_manager = hook_manager or create_default_hook_manager(
            state_manager,
            registrations=hook_registrations,
        )

        # Inner loop graph - created lazily
        self._inner_loop_graph = None

        self._current_build: BuildRequest | None = None
        self._current_task: asyncio.Task | None = None
        self._build_results: dict[str, BuildResult] = {}
        self._lock = asyncio.Lock()

        # Callback for notifying when build completes
        self._completion_callback: Callable[[BuildResult], None] | None = None

        # Callback for per-node progress within the currently active build
        # (there is only ever one, per the single-active-build contract
        # above). Set/cleared per-call by the caller starting a build, not
        # accumulated like HookManager registrations -- there is no
        # unregister on HookManager, so a dynamically-registered wildcard
        # hook per build would leak.
        self._progress_callback: Callable[[str, Mapping[str, Any]], Awaitable[None]] | None = None

    def _get_inner_loop_graph(self):
        """Lazily create the inner loop graph."""
        if self._inner_loop_graph is None:
            from ..graph import create_inner_loop_graph

            self._inner_loop_graph = create_inner_loop_graph(
                state_manager=self.state_manager,
                llm_client=self.llm_client,
                config=self.config,
                worktrees_dir=self.worktrees_dir,
                copilot_acp_manager=self.copilot_acp_manager,
                hook_manager=self.hook_manager,
            )
            logger.info("Created inner loop graph for builds")
        return self._inner_loop_graph

    @property
    def is_building(self) -> bool:
        """Check if a build is currently in progress."""
        return self._current_build is not None and self._current_task is not None

    @property
    def current_build_id(self) -> str | None:
        """Get the current build ID if one is running."""
        return self._current_build.build_id if self._current_build else None

    def get_build_status(self, build_id: str | None = None) -> BuildResult | None:
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
        build_settings: Any | None = None,  # BL-007: BuildSettings from decide node
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
                task_desc = (
                    self._current_build.task_description if self._current_build else "unknown"
                )
                raise RuntimeError(f"Build already in progress: {task_desc}")

            # Use build_id from settings if provided, otherwise generate
            if build_settings and hasattr(build_settings, "build_id"):
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

            # Start the build task promptly; do not hold the lock while awaiting the LLM.
            self._current_task = asyncio.create_task(
                self._run_build(build_id, initial_state),
                name=f"build_{build_id}",
            )

            logger.info("Started background build %s: %s", build_id, task_description)

        # Generate acknowledgment message via LLM outside the lock to avoid deadlocks.
        ack_message = await self._generate_acknowledgment(
            task_description=task_description,
            target_capabilities=target_capabilities,
            build_settings=build_settings,
        )

        return build_id, ack_message

    async def _generate_acknowledgment(
        self,
        task_description: str,
        target_capabilities: list[str],
        build_settings: Any | None = None,
    ) -> str:
        """Generate an LLM acknowledgment message for the build request."""
        try:
            # Include build settings info if available
            settings_info = ""
            if build_settings and hasattr(build_settings, "project_name"):
                settings_info = (
                    f"\nProject: {build_settings.project_name}"
                    f"\nType: {build_settings.build_type}"
                    f"\nDirectory: {build_settings.project_directory}"
                )
            if target_capabilities:
                settings_info += f"\nCapabilities: {', '.join(target_capabilities)}"

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
                f"🚀 Build Started\n\n"
                f"I've started working on your request: {task_description}\n\n"
                f"This is running in the background, so you can continue chatting. "
                f"Ask for build status anytime to check progress."
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
        repo_path = _resolve_repo_path(self.worktrees_dir)
        integration_branch = f"progenitor/integration/{build_id}"
        base_branch = "main"
        current_build = self._current_build  # Capture for type narrowing
        if current_build is None:
            raise RuntimeError("No current build set")
        result = BuildResult(
            build_id=build_id,
            status=BuildStatus.RUNNING,
            task_description=current_build.task_description,
            started_at=started_at,
        )

        # Initialize build_state before try block so it's available in except/finally
        build_state: dict[str, Any] = {}

        try:
            # Prepare state for inner loop - NO BACKLOG
            # The inner loop runs directly, not via work items

            # Ensure build_settings has status set to in_progress
            build_settings = initial_state.get("build_settings", {})
            if isinstance(build_settings, dict):
                build_settings = dict(build_settings)  # Copy to avoid mutation
                build_settings["status"] = "in_progress"
                build_settings["updated_at"] = utcnow_isoformat()

            build_state = {
                # Build identification
                "build_in_progress": True,
                "build_started_at": utcnow_isoformat(),
                # User input for context
                "current_input": current_build.user_input,
                # Build settings (from decide phase) with status set to in_progress
                # Canonical source for build_id and user_request
                "build_settings": build_settings,
                # Nested structure
                "task": {
                    "description": current_build.task_description,
                    "target_capabilities": current_build.target_capabilities,
                },
                "iteration": {
                    "current": 0,
                    "max": self.config.runtime.max_inner_iterations,
                    "start_time": None,
                },
                # Verification tracking (nested structure)
                "verification": {
                    "current_score": 0.0,
                    "current_passed": False,
                    "current_report": None,
                    "target_score": 0.8,
                },
                "spec": initial_state.get("spec", {}),
                "phases": {
                    "observe": {"ideal_state": None},
                    "think": {},
                    "plan": {},
                    "execute": {},
                    "verify": {"target_score": 0.8},
                },
                "history": {
                    "artefacts": [],
                    "verification_reports": [],
                    "lessons_learned": [],
                    "messages": [],
                },
                "best": {"iteration": None, "score": 0.0, "passed": False},
                "clarification": {
                    "needed": False,
                    "question": None,
                    "context": None,
                    "awaiting": False,
                    "response": None,
                },
                # Phase tracking
                "build_phase": "starting",
                # Git integration branch (keeps local main clean; PR targets main)
                "integration_branch": integration_branch,
                "integration_base_branch": base_branch,
            }

            await self.hook_manager.emit(
                "build_started",
                HookContext(
                    event="build_started",
                    build_id=build_id,
                    state=build_state,
                    metadata={
                        "task_description": current_build.task_description,
                        "target_capabilities": current_build.target_capabilities,
                    },
                ),
            )

            # Prepare integration branch before the inner loop starts (MERGE checks out target branch).
            base_ref = await _prepare_integration_branch(
                repo_path=repo_path,
                integration_branch=integration_branch,
                base_branch=base_branch,
            )
            build_state["integration_base_ref"] = base_ref

            # Save initial build state
            await self.state_manager.save_build_state(build_state)
            logger.info(f"BUILD RUNNER: Starting inner loop for build {build_id}")

            # Get the inner loop graph
            inner_graph = self._get_inner_loop_graph()

            # Run the inner loop graph
            thread_id = f"inner_loop_{build_id}"

            async for event in inner_graph.astream(
                cast(Any, build_state),  # build_state conforms to ProgenitorState
                config={"configurable": {"thread_id": thread_id}},
            ):
                for node_name, node_output in event.items():
                    if isinstance(node_output, dict):
                        # Deep merge to preserve nested structures (phases, history, etc.)
                        deep_merge(build_state, node_output)

                        # Save checkpoint after each node
                        await self.state_manager.save_build_state(build_state)

                        # Track iterations (verify node marks end of iteration)
                        if node_name == "verify":
                            result.iterations = build_state.get("iteration", {}).get("current", 0)
                            result.verification_score = (
                                build_state.get("phases", {}).get("verify", {}).get("score", 0)
                            )

                        # Log progress
                        logger.info(
                            "BUILD %s: node=%s, iteration=%d, score=%.2f, phase=%s",
                            build_id,
                            node_name,
                            build_state.get("iteration", {}).get("current", 0),
                            build_state.get("phases", {}).get("verify", {}).get("score", 0),
                            build_state.get("build_phase", "unknown"),
                        )

                        if self._progress_callback is not None:
                            try:
                                await self._progress_callback(node_name, build_state)
                            except Exception:
                                logger.warning(
                                    "Progress callback failed for build %s node=%s",
                                    build_id,
                                    node_name,
                                    exc_info=True,
                                )

                        # Check for errors
                        if build_state.get("build_error"):
                            raise RuntimeError(build_state["build_error"])

            # Note: build artifacts live in the main repo now (no submodules), so
            # PR creation is handled by the inner-loop PUBLISH node.

            _assert_expected_output_exists(repo_path, build_state)
            result.pr_url = _assert_pr_created_if_required(build_state)

            # Build completed successfully
            result.status = BuildStatus.COMPLETED
            result.completed_at = utcnow()
            result.verification_score = build_state.get("verification", {}).get("current_score", 0)
            result.agent_name = build_state.get("agent_name") or build_state.get(
                "build_settings", {}
            ).get("project_name")

            # Extract success criteria results
            verification_report: Any = (
                build_state.get("verification", {}).get("current_report") or {}
            )
            if hasattr(verification_report, "model_dump"):
                verification_report = verification_report.model_dump()
            if not isinstance(verification_report, dict):
                verification_report = {}

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
            await self.hook_manager.emit(
                "build_completed",
                HookContext(
                    event="build_completed",
                    build_id=build_id,
                    state=build_state,
                    metadata={
                        "iterations": result.iterations,
                        "verification_score": result.verification_score,
                        "pr_url": result.pr_url,
                    },
                ),
            )

        except asyncio.CancelledError:
            result.status = BuildStatus.CANCELLED
            result.completed_at = utcnow()
            result.error_message = "Build was cancelled"
            logger.info("Build %s was cancelled", build_id)
            await self.hook_manager.emit(
                "build_cancelled",
                HookContext(
                    event="build_cancelled",
                    build_id=build_id,
                    state=build_state,
                    error=result.error_message,
                ),
            )

            # Minimal update for suspension - only set flags, don't overwrite state
            try:
                current_iteration = build_state.get("iteration", {}).get("current", 0)
                suspension_event = {
                    "event": "suspended",
                    "timestamp": utcnow_isoformat(),
                    "reason": "shutdown_requested",
                    "iteration": current_iteration,
                }

                # Update build_settings status to suspended
                existing_build_settings = build_state.get("build_settings", {})
                if isinstance(existing_build_settings, dict):
                    existing_build_settings["status"] = "suspended"
                    existing_build_settings["error_message"] = "Cancelled by signal"
                    existing_build_settings["updated_at"] = utcnow_isoformat()

                await self.state_manager.update_build_state(
                    {
                        "build_in_progress": False,
                        "build_phase": "suspended",
                        "build_settings": existing_build_settings,
                        "build_messages": build_state.get("build_messages", [])
                        + [suspension_event],
                    }
                )
                logger.info("Build %s suspended at iteration %d", build_id, current_iteration)
            except Exception as e:
                logger.warning("Failed to record suspension event: %s", e)

        except Exception as e:
            result.status = BuildStatus.FAILED
            result.completed_at = utcnow()
            result.error_message = str(e)
            logger.error("Build %s failed: %s", build_id, e, exc_info=True)
            await self.hook_manager.emit(
                "build_failed",
                HookContext(
                    event="build_failed",
                    build_id=build_id,
                    state=build_state,
                    metadata={"error_type": type(e).__name__},
                    error=str(e),
                ),
            )

            # Update build_settings status to failed
            try:
                existing_build_settings = build_state.get("build_settings", {})
                if isinstance(existing_build_settings, dict):
                    existing_build_settings["status"] = "failed"
                    existing_build_settings["error_message"] = str(e)
                    existing_build_settings["updated_at"] = utcnow_isoformat()
                await self.state_manager.update_build_state(
                    {
                        "build_phase": "failed",
                        "build_settings": existing_build_settings,
                    }
                )
            except Exception as update_err:
                logger.warning("Failed to update build_settings status: %s", update_err)

        finally:
            # Store result and clean up
            self._build_results[build_id] = result

            async with self._lock:
                self._current_build = None
                self._current_task = None

            # Best-effort: return repo to main to reduce surprises for subsequent operations.
            with contextlib.suppress(Exception):
                await _run_git(["checkout", "main"], cwd=repo_path, timeout_seconds=30.0)

            # Only update last_build_result in finally - don't overwrite build_phase/build_settings
            # The CancelledError/Exception handlers already set those appropriately
            # For completed builds (not cancelled/failed), update build_settings here
            if result.status == BuildStatus.COMPLETED:
                # Update build_settings status to completed
                try:
                    existing_build_settings = build_state.get("build_settings", {})
                    if isinstance(existing_build_settings, dict):
                        existing_build_settings["status"] = "completed"
                        existing_build_settings["updated_at"] = utcnow_isoformat()
                        existing_build_settings["completed_at"] = utcnow_isoformat()
                except Exception:
                    existing_build_settings = {}

                await self.state_manager.update_build_state(
                    {
                        "build_in_progress": False,
                        "build_phase": "completed",
                        "build_settings": existing_build_settings,
                        "last_build_result": {
                            "build_id": build_id,
                            "status": result.status.value,
                            "completed_at": utcnow_isoformat(),
                            "output_summary": result.output_summary,
                            "error_message": result.error_message,
                        },
                    }
                )
            elif result.status == BuildStatus.FAILED:
                # Already updated in the except block, just set last_build_result
                await self.state_manager.update_build_state(
                    {
                        "build_in_progress": False,
                        "last_build_result": {
                            "build_id": build_id,
                            "status": result.status.value,
                            "completed_at": utcnow_isoformat(),
                            "output_summary": result.output_summary,
                            "error_message": result.error_message,
                        },
                    }
                )
            else:
                # For cancelled/suspended builds, just update the last_build_result
                # Don't touch build_in_progress or build_phase (already set in CancelledError handler)
                await self.state_manager.update_build_state(
                    {
                        "last_build_result": {
                            "build_id": build_id,
                            "status": "cancelled",
                            "completed_at": utcnow_isoformat(),
                            "output_summary": result.output_summary,
                            "error_message": "Build was cancelled",
                        },
                    }
                )

            # Also update current_state to clear the build reference
            await self.state_manager.update_current_state(
                {
                    "current_build_request": None,
                }
            )

            # Trigger completion callback if set
            if self._completion_callback:
                try:
                    self._completion_callback(result)
                except Exception as e:
                    logger.error("Build completion callback failed: %s", e)

    async def cancel_build(self, build_id: str | None = None) -> bool:
        """Cancel a running build.

        Args:
            build_id: Specific build to cancel, or None for current build.

        Returns:
            True if build was cancelled, False if no matching build found.
        """
        async with self._lock:
            if not self._current_task:
                return False

            if build_id and self._current_build and self._current_build.build_id != build_id:
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

    def set_progress_callback(
        self,
        callback: Callable[[str, Mapping[str, Any]], Awaitable[None]] | None,
    ) -> None:
        """Set (or clear, with None) the per-node progress callback for the
        build about to start. Invoked once per inner-loop node transition
        with (node_name, build_state). Callers should clear this once their
        build finishes so a stale callback isn't invoked for a later build.
        """
        self._progress_callback = callback

    def get_recent_builds(self, limit: int = 5) -> list[BuildResult]:
        """Get recent build results."""
        results = list(self._build_results.values())
        return results[-limit:]


# Module-level reference for access from nodes
_background_runner: BackgroundBuildRunner | None = None


def get_background_runner() -> BackgroundBuildRunner | None:
    """Get the background build runner instance."""
    return _background_runner


def set_background_runner(runner: BackgroundBuildRunner | None) -> None:
    """Set the background build runner instance."""
    global _background_runner
    _background_runner = runner
