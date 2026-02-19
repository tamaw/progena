"""
Build Tool (system tool).

Starts a background build (agent or MCP) using existing Decide logic for
confidence gating and build-settings extraction.
"""

import logging
from typing import Any

from ..env_utils import env_float
from ..runtime.time_utils import utcnow_isoformat
from .protocol import BaseSystemTool, ToolContext, ToolResult

logger = logging.getLogger(__name__)


class BuildTool(BaseSystemTool):
    @property
    def name(self) -> str:
        return "build"

    @property
    def description(self) -> str:
        return (
            "Start a background build for an A2A agent or MCP tool/server. "
            "Uses Decide confidence gating to avoid false positives."
        )

    @property
    def keywords(self) -> list[str]:
        # Intentionally empty: tool should be invoked by LLM tool-calling, not heuristics.
        return []

    async def execute(self, context: ToolContext) -> ToolResult:
        args = context.tool_args or {}
        request = (args.get("request") or context.user_input or "").strip()
        if not request:
            return ToolResult.fail("Missing required argument: request")

        # Lazy imports to avoid circular deps (tools registry <-> decide node).
        from ..nodes import decide as decide_node
        from ..runtime.background_build import get_background_runner

        tool_context = ""
        if context.tool_registry and hasattr(context.tool_registry, "get_tool_context"):
            try:
                tool_context = context.tool_registry.get_tool_context()
            except Exception:
                tool_context = ""

        # Minimal history (Decide helper already expects OpenAI-style {role,content}).
        history: list[dict[str, str]] = []
        state_messages = (context.state or {}).get("messages", [])
        for msg in state_messages[-10:]:
            if isinstance(msg, dict):
                role = str(msg.get("role", "user"))
                content = str(msg.get("content", ""))
            elif hasattr(msg, "role") and hasattr(msg, "content"):
                role = str(msg.role)
                content = str(msg.content)
            else:
                continue
            history.append({"role": role, "content": content})

        decision_data = await decide_node._request_decision_data(
            llm_client=context.llm_client,
            user_input=request,
            conversation_history=history,
            tool_context=tool_context,
        )

        action = str(decision_data.get("action", "")).lower().strip()
        confidence_raw: Any = decision_data.get("confidence", 0.0)
        try:
            confidence = float(confidence_raw)
        except Exception:
            confidence = 0.0

        threshold = env_float("PROGENITOR_BUILD_CONFIDENCE_THRESHOLD", 0.95)
        if action not in ("build_agent", "build_tool", "upgrade"):
            return ToolResult.ok(
                {
                    "started": False,
                    "reason": f"Decide action was not a build: {action or '(empty)'}",
                    "confidence": confidence,
                    "threshold": threshold,
                }
            )

        if confidence < threshold:
            return ToolResult.ok(
                {
                    "started": False,
                    "reason": "Build confidence below threshold",
                    "confidence": confidence,
                    "threshold": threshold,
                }
            )

        runner = get_background_runner()
        if not runner:
            return ToolResult.fail("Background build runner unavailable")

        build_settings = await decide_node._generate_build_settings(
            user_input=request,
            llm_client=context.llm_client,
            llm_action=action,
            decision_data=decision_data,
        )

        task_description = f"Build {build_settings.build_type}: {build_settings.project_name}"
        target_capabilities = decide_node._infer_target_capabilities(request)

        try:
            build_id, ack_message = await runner.start_build(
                user_input=request,
                task_description=task_description,
                target_capabilities=target_capabilities,
                initial_state=dict(context.state or {}),
                build_settings=build_settings,
            )
        except RuntimeError as exc:
            return ToolResult.ok(
                {
                    "started": False,
                    "reason": str(exc),
                    "confidence": confidence,
                    "threshold": threshold,
                }
            )

        # Mirror the minimal state signals Decide used to set, so status tools + UI can reflect it.
        state_updates = {
            "build_in_progress": True,
            "task": {
                "description": task_description,
                "target_capabilities": target_capabilities,
            },
            "build_started_at": utcnow_isoformat(),
            "build_settings": build_settings.model_dump(),
        }

        return ToolResult.ok(
            {
                "started": True,
                "build_id": build_id,
                "project_name": build_settings.project_name,
                "build_type": build_settings.build_type,
                "confidence": confidence,
                "threshold": threshold,
                "ack_message": ack_message,
            },
            state_updates=state_updates,
        )

