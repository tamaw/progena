"""
Build Status - Check the current build status, progress, and messages.
"""

import logging
from typing import Any

from .protocol import BaseSystemTool, ToolContext, ToolResult
from ..state.helpers import get_ideal_state

logger = logging.getLogger(__name__)


class BuildStatusTool(BaseSystemTool):
    """Check the current build status, progress, and messages."""

    @property
    def name(self) -> str:
        return "build_status"

    @property
    def description(self) -> str:
        return "Check the current build status, progress, and messages"

    @property
    def keywords(self) -> list[str]:
        return [
            "build status",
            "build progress",
            "build state",
            "what are you building",
            "what's being built",
            "current build",
            "is there a build",
            "check build",
            "building status",
            "how is the build",
            "how's the build",
            "build update",
            "update on build",
            "building progress",
            "are you building",
        ]

    async def execute(self, context: ToolContext) -> ToolResult:
        from ..runtime.background_build import get_background_runner

        runner = get_background_runner()

        if runner:
            build_result = runner.get_build_status()

            if build_result:
                result = {
                    "build_in_progress": build_result.status.value in ("pending", "running"),
                    "build_id": build_result.build_id,
                    "status": build_result.status.value,
                    "task_description": build_result.task_description,
                    "started_at": build_result.started_at.isoformat() if build_result.started_at else None,
                    "completed_at": build_result.completed_at.isoformat() if build_result.completed_at else None,
                    "iterations": build_result.iterations,
                    "verification_score": build_result.verification_score,
                    "output_summary": build_result.output_summary,
                    "error_message": build_result.error_message,
                    "agent_name": build_result.agent_name,
                    "success_criteria_met": build_result.success_criteria_met,
                    "success_criteria_unmet": build_result.success_criteria_unmet,
                }

                # Get recent builds
                recent_builds = runner.get_recent_builds(limit=3)
                if len(recent_builds) > 1:
                    result["recent_builds"] = [
                        {
                            "build_id": b.build_id,
                            "status": b.status.value,
                            "task_description": b.task_description[:50] + "..." if len(b.task_description) > 50 else b.task_description,
                        }
                        for b in recent_builds[:-1]
                    ]

                return ToolResult.ok(result)

        # Use in-memory state (context.state is the authoritative source)
        effective_state: dict[str, Any] = dict(context.state) if context.state else {}

        if not effective_state:
            return ToolResult.ok({
                "build_in_progress": False,
                "message": "No build in progress and no recent builds found.",
            })

        build_in_progress = effective_state.get("build_in_progress", False)
        build_phase = effective_state.get("build_phase")
        build_task = effective_state.get("build_task_description")
        build_started = effective_state.get("build_started_at")
        build_id = (effective_state.get("build_settings") or {}).get("build_id")
        current_iteration = effective_state.get("iteration", {}).get("current", 0)
        verification_score = effective_state.get("verification", {}).get("current_score", 0.0)
        build_messages = effective_state.get("build_messages", [])
        ideal_state = get_ideal_state(effective_state)

        recent_messages = build_messages[-5:] if build_messages else []

        result = {
            "build_in_progress": build_in_progress,
            "build_id": build_id,
            "phase": build_phase,
            "task_description": build_task,
            "started_at": build_started,
            "current_iteration": current_iteration,
            "verification_score": verification_score,
            "message_count": len(build_messages),
            "recent_messages": recent_messages,
        }

        ideal_state_dict = None
        if ideal_state:
            if isinstance(ideal_state, dict):
                ideal_state_dict = ideal_state
            elif hasattr(ideal_state, "model_dump"):
                ideal_state_dict = ideal_state.model_dump()

        if ideal_state_dict:
            result["target_score"] = ideal_state_dict.get("stop_conditions", [{}])[0].get("threshold", 0.8)
            result["success_criteria"] = [
                c.get("description") for c in ideal_state_dict.get("success_criteria", [])
            ]

        return ToolResult.ok(result)
