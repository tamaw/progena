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
import json
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from pydantic import BaseModel, Field

from ..utils import env_bool
from ..tools.gh import gh_pr_create
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
        if (
            key in base
            and isinstance(base[key], dict)
            and isinstance(value, dict)
        ):
            # Recursively merge nested dicts
            deep_merge(base[key], value)
        else:
            # Override with new value
            base[key] = value
    return base


class PRDraft(BaseModel):
    """LLM-drafted PR metadata."""

    title: str = Field(min_length=1)
    body: str = Field(min_length=1)


async def _draft_pr_with_llm(
    llm_client,
    *,
    task_description: str,
    build_id: str,
    verification_score: float,
    target_score: float,
    verification_report: Any,
) -> PRDraft | None:
    """Ask the LLM to draft a PR title/body (no tool use)."""
    try:
        system_prompt = (
            "You draft high-quality GitHub pull request metadata.\n"
            "Return JSON only with keys: title (string), body (string).\n"
            "Body should include a concise summary and any notable verification results."
        )
        user_prompt = {
            "task_description": task_description,
            "build_id": build_id,
            "verification_score": verification_score,
            "target_score": target_score,
            "verification_report": verification_report,
        }

        if hasattr(llm_client, "chat_structured"):
            parsed = await llm_client.chat_structured(
                messages=[{"role": "user", "content": json.dumps(user_prompt)}],
                response_model=PRDraft,
                schema_name="pr_draft",
                system_prompt=system_prompt,
                temperature=0.3,
            )
            return PRDraft.model_validate(parsed.model_dump() if hasattr(parsed, "model_dump") else parsed)

        # Fallback to JSON response_format if available
        response_format = getattr(llm_client, "_get_json_response_format", None)
        if callable(response_format):
            rf = llm_client._get_json_response_format(PRDraft, "pr_draft")
            raw = await llm_client.chat(
                messages=[{"role": "user", "content": json.dumps(user_prompt)}],
                system_prompt=system_prompt,
                temperature=0.3,
                response_format=rf,
            )
            return PRDraft.model_validate(json.loads(raw))
    except Exception:
        return None
    return None


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
    result = await _run_git(["checkout", "-B", integration_branch, base_ref], cwd=repo_path, timeout_seconds=30.0)
    if result["returncode"] != 0:
        raise RuntimeError(f"Failed to checkout integration branch {integration_branch}: {result['stderr']}".strip())

    return base_ref


def _submodule_roots_from_build_settings(build_settings: Any) -> list[str]:
    """Return which submodule roots are relevant for this build (build/agents or build/mcp)."""
    if not isinstance(build_settings, dict):
        return []
    proj_dir = (build_settings.get("project_directory") or "").replace("\\", "/").strip("/")
    if proj_dir.startswith("build/agents/") or proj_dir == "build/agents":
        return ["build/agents"]
    if proj_dir.startswith("build/mcp/") or proj_dir == "build/mcp":
        return ["build/mcp"]
    return []


async def _commit_push_pr_in_submodule(
    *,
    llm_client,
    worktree_path: Path,
    submodule_root: str,
    build_id: str,
    task_description: str,
    verification_score: float,
    target_score: float,
    verification_report: Any,
) -> dict[str, Any]:
    """Commit and open PR in a submodule repo, running commands inside the submodule."""
    sub_repo = (worktree_path / Path(submodule_root)).resolve()
    if not (sub_repo / ".git").exists():
        return {"status": "not_a_repo", "submodule_root": submodule_root, "path": str(sub_repo)}

    status = await _run_git(["status", "--porcelain"], cwd=sub_repo, timeout_seconds=20.0)
    if status["returncode"] != 0:
        return {"status": "status_failed", "submodule_root": submodule_root, "error": status["stderr"].strip()}
    if not status["stdout"].strip():
        return {"status": "no_changes", "submodule_root": submodule_root}

    branch = f"progenitor/{build_id}"
    await _run_git(["fetch", "origin", "main"], cwd=sub_repo, timeout_seconds=60.0)
    checkout = await _run_git(["checkout", "-B", branch], cwd=sub_repo, timeout_seconds=20.0)
    if checkout["returncode"] != 0:
        return {"status": "checkout_failed", "submodule_root": submodule_root, "error": checkout["stderr"].strip()}

    await _run_git(["add", "-A"], cwd=sub_repo, timeout_seconds=20.0)
    # Ensure identity exists for non-interactive environments.
    await _run_git(["config", "user.email", "progenitor@local"], cwd=sub_repo, timeout_seconds=10.0)
    await _run_git(["config", "user.name", "Progenitor"], cwd=sub_repo, timeout_seconds=10.0)
    commit_msg = f"Progenitor: {task_description}"
    commit = await _run_git(["commit", "-m", commit_msg], cwd=sub_repo, timeout_seconds=30.0)
    if commit["returncode"] != 0:
        # If another step committed already, treat as non-fatal.
        if "nothing to commit" in (commit["stdout"] + commit["stderr"]).lower():
            return {"status": "no_changes", "submodule_root": submodule_root}
        return {"status": "commit_failed", "submodule_root": submodule_root, "error": (commit["stderr"] or commit["stdout"]).strip()}

    push = await _run_git(["push", "-u", "origin", branch], cwd=sub_repo, timeout_seconds=120.0)
    if push["returncode"] != 0:
        return {"status": "push_failed", "submodule_root": submodule_root, "error": (push["stderr"] or push["stdout"]).strip()}

    title = f"Progenitor: {task_description}"
    body = (
        "Automated change from Progenitor inner build loop.\n\n"
        f"- Build ID: {build_id}\n"
        f"- Verification: {verification_score:.2f} (target {target_score:.2f})\n"
    )

    if env_bool("PROGENITOR_AUTO_PR_USE_LLM", True):
        drafted = await _draft_pr_with_llm(
            llm_client,
            task_description=f"{task_description} ({submodule_root})",
            build_id=build_id,
            verification_score=verification_score,
            target_score=target_score,
            verification_report=verification_report,
        )
        if drafted:
            title = drafted.title.strip() or title
            body = drafted.body.strip() or body

    draft = env_bool("PROGENITOR_AUTO_PR_DRAFT", True)
    pr_info = await gh_pr_create(
        repo_path=sub_repo,
        head_branch=branch,
        base_branch="main",
        title=title,
        body=body,
        draft=draft,
    )
    pr_info.update({"submodule_root": submodule_root, "branch": branch})
    return pr_info


async def _cleanup_worktrees(*, repo_path: Path, worktrees: list[str]) -> None:
    """Best-effort removal of git worktrees recorded during the build."""
    for wt in worktrees:
        try:
            await _run_git(["worktree", "remove", "--force", wt], cwd=repo_path, timeout_seconds=60.0)
        except Exception:
            continue

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
    ):
        self.state_manager = state_manager
        self.config = config
        self.llm_client = llm_client
        self.worktrees_dir = worktrees_dir or Path("worktrees")
        self.copilot_acp_manager = copilot_acp_manager

        # Inner loop graph - created lazily
        self._inner_loop_graph = None

        self._current_build: BuildRequest | None = None
        self._current_task: asyncio.Task | None = None
        self._build_results: dict[str, BuildResult] = {}
        self._lock = asyncio.Lock()

        # Callback for notifying when build completes
        self._completion_callback: Callable[[BuildResult], None] | None = None

    def _get_inner_loop_graph(self):
        """Lazily create the inner loop graph."""
        if self._inner_loop_graph is None:
            from ..graph_build_loop import create_inner_loop_graph
            self._inner_loop_graph = create_inner_loop_graph(
                state_manager=self.state_manager,
                llm_client=self.llm_client,
                config=self.config,
                worktrees_dir=self.worktrees_dir,
                copilot_acp_manager=self.copilot_acp_manager,
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
                task_desc = self._current_build.task_description if self._current_build else "unknown"
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
                    build_settings.model_dump() if hasattr(build_settings, "model_dump") else build_settings
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
            if build_settings and hasattr(build_settings, 'project_name'):
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
        repo_path = Path(__file__).resolve().parents[3]
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
                "phases": {"observe": {"ideal_state": None}, "think": {}, "plan": {}, "execute": {}, "verify": {"target_score": 0.8}},
                "history": {"artefacts": [], "verification_reports": [], "lessons_learned": [], "messages": []},
                "best": {"iteration": None, "score": 0.0, "passed": False},
                "clarification": {"needed": False, "question": None, "context": None, "awaiting": False, "response": None},

                 # Phase tracking
                 "build_phase": "starting",

                 # Git integration branch (keeps local main clean; PR targets main)
                 "integration_branch": integration_branch,
                 "integration_base_branch": base_branch,
             }

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
                            result.verification_score = build_state.get("phases", {}).get("verify", {}).get("score", 0)

                        # Log progress
                        logger.info(
                            "BUILD %s: node=%s, iteration=%d, score=%.2f, phase=%s",
                            build_id,
                            node_name,
                            build_state.get("iteration", {}).get("current", 0),
                            build_state.get("phases", {}).get("verify", {}).get("score", 0),
                            build_state.get("build_phase", "unknown"),
                        )

                        # Check for errors
                        if build_state.get("build_error"):
                            raise RuntimeError(build_state["build_error"])

            # Auto-push + PR(s) on success (best-effort; does not fail the build)
            verification = build_state.get("verification", {}) or {}
            score = float(verification.get("current_score") or 0.0)
            target = float(verification.get("target_score") or 0.8)
            passed = bool(verification.get("current_passed", False))

            if env_bool("PROGENITOR_AUTO_PR_ON_SUCCESS", False) and passed and score >= target:
                # Publish PR(s) directly in the target submodule repo (build/agents or build/mcp).
                build_settings_dict = build_state.get("build_settings") or {}
                submodule_roots = _submodule_roots_from_build_settings(build_settings_dict)
                verification_report = verification.get("current_report")

                worktrees = list(build_state.get("worktrees_to_cleanup", []))
                if not worktrees:
                    # Fallback: attempt from merge_queue paths if present.
                    for q in (build_state.get("merge_queue") or []):
                        if isinstance(q, dict) and q.get("worktree_path"):
                            worktrees.append(q["worktree_path"])

                pr_results: list[dict[str, Any]] = []
                for wt in worktrees:
                    wt_path = Path(wt)
                    for root in submodule_roots:
                        pr_results.append(
                            await _commit_push_pr_in_submodule(
                                llm_client=self.llm_client,
                                worktree_path=wt_path,
                                submodule_root=root,
                                build_id=build_id,
                                task_description=current_build.task_description,
                                verification_score=score,
                                target_score=target,
                                verification_report=verification_report,
                            )
                        )

                build_state["submodule_pull_requests"] = pr_results
                await self.state_manager.update_build_state({"submodule_pull_requests": pr_results})

                # Best-effort cleanup after publishing.
                await _cleanup_worktrees(repo_path=repo_path, worktrees=worktrees)
                await self.state_manager.update_build_state({"worktrees_to_cleanup": []})

            # Build completed successfully
            result.status = BuildStatus.COMPLETED
            result.completed_at = utcnow()
            result.verification_score = build_state.get("verification", {}).get("current_score", 0)
            result.agent_name = build_state.get("agent_name") or build_state.get("build_settings", {}).get("project_name")

            # Extract success criteria results
            verification_report = build_state.get("verification", {}).get("current_report", {})
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

                await self.state_manager.update_build_state({
                    "build_in_progress": False,
                    "build_phase": "suspended",
                    "build_settings": existing_build_settings,
                    "build_messages": build_state.get("build_messages", []) + [suspension_event],
                })
                logger.info("Build %s suspended at iteration %d", build_id, current_iteration)
            except Exception as e:
                logger.warning("Failed to record suspension event: %s", e)

        except Exception as e:
            result.status = BuildStatus.FAILED
            result.completed_at = utcnow()
            result.error_message = str(e)
            logger.error("Build %s failed: %s", build_id, e, exc_info=True)

            # Update build_settings status to failed
            try:
                existing_build_settings = build_state.get("build_settings", {})
                if isinstance(existing_build_settings, dict):
                    existing_build_settings["status"] = "failed"
                    existing_build_settings["error_message"] = str(e)
                    existing_build_settings["updated_at"] = utcnow_isoformat()
                await self.state_manager.update_build_state({
                    "build_phase": "failed",
                    "build_settings": existing_build_settings,
                })
            except Exception as update_err:
                logger.warning("Failed to update build_settings status: %s", update_err)

        finally:
            # Store result and clean up
            self._build_results[build_id] = result

            async with self._lock:
                self._current_build = None
                self._current_task = None

            # Best-effort: return repo to main to reduce surprises for subsequent operations.
            try:
                repo_path = Path(__file__).resolve().parents[3]
                await _run_git(["checkout", "main"], cwd=repo_path, timeout_seconds=30.0)
            except Exception:
                pass

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

                await self.state_manager.update_build_state({
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
                })
            elif result.status == BuildStatus.FAILED:
                # Already updated in the except block, just set last_build_result
                await self.state_manager.update_build_state({
                    "build_in_progress": False,
                    "last_build_result": {
                        "build_id": build_id,
                        "status": result.status.value,
                        "completed_at": utcnow_isoformat(),
                        "output_summary": result.output_summary,
                        "error_message": result.error_message,
                    },
                })
            else:
                # For cancelled/suspended builds, just update the last_build_result
                # Don't touch build_in_progress or build_phase (already set in CancelledError handler)
                await self.state_manager.update_build_state({
                    "last_build_result": {
                        "build_id": build_id,
                        "status": "cancelled",
                        "completed_at": utcnow_isoformat(),
                        "output_summary": result.output_summary,
                        "error_message": "Build was cancelled",
                    },
                })

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
