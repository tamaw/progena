"""
System Tool Implementations.

Concrete implementations of system tools that can be executed inline.
"""

import logging
import os
from pathlib import Path
from typing import Any

from .protocol import BaseSystemTool, ToolContext, ToolResult
from ...runtime.time_utils import utcnow_isoformat
from ...state.helpers import get_ideal_state
from ...state.models import generate_run_id, ProgenitorState
from ...env_utils import env_int, env_bool

logger = logging.getLogger(__name__)


class ListToolsTool(BaseSystemTool):
    """List all available tools including system tools, agents, and MCP tools."""

    @property
    def name(self) -> str:
        return "list_tools"

    @property
    def description(self) -> str:
        return "List all available tools and their capabilities"

    async def execute(self, context: ToolContext) -> ToolResult:
        from .registry import get_all_system_tools

        system_tools = [tool.to_dict() for tool in get_all_system_tools()]

        # Get registered MCP tools from registry
        mcp_tools = context.tool_registry.get_tools()
        mcp_tool_entries = [
            {
                "name": tool.get("name"),
                "description": tool.get("description", ""),
                "type": "mcp_tool",
            }
            for tool in mcp_tools
        ]

        return ToolResult.ok({
            "system_tools": system_tools,
            "mcp_tools": mcp_tool_entries,
            "total_count": len(system_tools) + len(mcp_tool_entries),
        })


class RediscoverAgentsTool(BaseSystemTool):
    """Scan the agents folder, analyze with LLM, and update registry.json."""

    @property
    def name(self) -> str:
        return "rediscover_agents"

    @property
    def description(self) -> str:
        return "Scan the agents folder, analyze with LLM, and update registry.json"

    async def execute(self, context: ToolContext) -> ToolResult:
        from ...tools.agent_discovery import discover_agents

        # Get agents directory
        project_root = Path(__file__).parent.parent.parent.parent.parent
        agents_dir = project_root / "app" / "agents"

        logger.info("Executing system tool: rediscover_agents")

        result = await discover_agents(
            agents_dir=agents_dir,
            llm_client=context.llm_client,
            registry=context.tool_registry,
        )

        return ToolResult.ok(result)


class ListAgentsTool(BaseSystemTool):
    """List all registered agents with their status."""

    @property
    def name(self) -> str:
        return "list_agents"

    @property
    def description(self) -> str:
        return "List all registered agents with their status"

    async def execute(self, context: ToolContext) -> ToolResult:
        agents = context.tool_registry.get_agents()
        all_agents = [
            {
                "name": agent.get("name"),
                "description": agent.get("description"),
                "status": agent.get("status", "unknown"),
                "transport": agent.get("transport"),
                "port": agent.get("port"),
                "endpoint": agent.get("endpoint"),
            }
            for agent in agents
        ]

        running = [a for a in agents if a.get("status") == "running"]

        return ToolResult.ok({
            "agents": all_agents,
            "running_count": len(running),
            "total_count": len(all_agents),
        })


class NewConversationTool(BaseSystemTool):
    """Start a new conversation by resetting run_id and clearing session state."""

    @property
    def name(self) -> str:
        return "new_conversation"

    @property
    def description(self) -> str:
        return "Start a new conversation (fresh run_id, clear chat history)"

    async def execute(self, context: ToolContext) -> ToolResult:
        if not context.state_manager and context.state is None:
            return ToolResult.fail("No state manager or in-memory state available.")

        previous_state = {}
        if context.state is not None:
            previous_state = context.state
        elif context.state_manager:
            previous_state = await context.state_manager.get_current_state()

        previous_run_id = previous_state.get("run_id") if isinstance(previous_state, dict) else None
        previous_messages = previous_state.get("messages", []) if isinstance(previous_state, dict) else []
        messages_cleared = len(previous_messages or [])

        new_run_id = generate_run_id()

        reset_state: ProgenitorState = {
            "run_id": new_run_id,
            "cycle_count": 0,
            "actions_this_cycle": 0,
            "shutdown_requested": False,
            "last_checkpoint": None,
            "messages": [],
            "attachments": [],
            "tool_calls": [],
            "current_input": None,
            "pending_response": None,
            "current_decision": None,
            "current_build_request": None,
        }

        if context.state_manager:
            await context.state_manager.update_current_state(reset_state)

        if context.state is not None:
            context.state.update(reset_state)

        logger.info(
            "New conversation started (previous_run_id=%s, new_run_id=%s, messages_cleared=%d)",
            previous_run_id,
            new_run_id,
            messages_cleared,
        )

        return ToolResult.ok({
            "reset": True,
            "run_id": new_run_id,
            "new_run_id": new_run_id,
            "previous_run_id": previous_run_id,
            "messages_cleared": messages_cleared,
        })


class BuildStatusTool(BaseSystemTool):
    """Check the current build status, progress, and messages."""

    @property
    def name(self) -> str:
        return "build_status"

    @property
    def description(self) -> str:
        return "Check the current build status, progress, and messages"

    async def execute(self, context: ToolContext) -> ToolResult:
        from ...runtime.background_build import get_background_runner

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

        # Read from persisted state files via state_manager
        persisted_state = None
        if context.state_manager:
            try:
                persisted_state = await context.state_manager.get_build_state()
            except Exception as e:
                logger.warning("Failed to read persisted state: %s", e)

        # Merge in-memory state with persisted state
        effective_state: dict[str, Any] = {}
        if persisted_state:
            effective_state.update(persisted_state)
        if context.state:
            effective_state.update(context.state)

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


class CurrentTimeTool(BaseSystemTool):
    """Return the current UTC time."""

    @property
    def name(self) -> str:
        return "current_time"

    @property
    def description(self) -> str:
        return "Return the current UTC time"

    async def execute(self, context: ToolContext) -> ToolResult:
        return ToolResult.ok({
            "utc_time": utcnow_isoformat(),
            "timezone": "UTC",
        })


class ListScheduleTool(BaseSystemTool):
    """List all scheduled tasks and reminders."""

    @property
    def name(self) -> str:
        return "list_schedule"

    @property
    def description(self) -> str:
        return "List all scheduled tasks and reminders"

    async def execute(self, context: ToolContext) -> ToolResult:
        if context.state is None:
            return ToolResult.ok({
                "scheduled_tasks": [],
                "total_count": 0,
                "message": "No state available - schedule information unavailable",
            })

        schedule = context.state.get("schedule", [])

        # Convert schedule entries to readable format
        tasks = []
        for item in schedule:
            if hasattr(item, "model_dump"):
                item_dict = item.model_dump()
            elif isinstance(item, dict):
                item_dict = item
            else:
                continue

            tasks.append({
                "id": item_dict.get("id"),
                "description": item_dict.get("description", "No description"),
                "cron_expression": item_dict.get("cron_expression"),
                "next_run": item_dict.get("next_run"),
                "action_type": item_dict.get("action_type"),
                "enabled": item_dict.get("enabled", True),
            })

        return ToolResult.ok({
            "scheduled_tasks": tasks,
            "total_count": len(tasks),
        })


class SelfReflectTool(BaseSystemTool):
    """Introspect own source code to understand how Progenitor works."""

    @property
    def name(self) -> str:
        return "self_reflect"

    @property
    def description(self) -> str:
        return "Introspect own source code to understand how Progenitor works"

    async def execute(self, context: ToolContext) -> ToolResult:
        from ...tools.self_reflect import SelfReflection

        logger.info("Executing system tool: self_reflect")
        reflection = SelfReflection(llm_client=context.llm_client)

        # For inline execution, return module listing
        modules = reflection.list_modules()

        return ToolResult.ok({
            "description": "Progenitor self-reflection - exploring own source code",
            "root_path": str(reflection.root),
            "modules": [
                {
                    "name": m.name,
                    "path": m.path,
                    "is_directory": m.is_directory,
                    "description": m.description,
                }
                for m in modules
            ],
            "module_count": len([m for m in modules if m.is_directory]),
            "file_count": len([m for m in modules if not m.is_directory]),
            "tip": "Use follow-up questions to explore specific modules or search for code patterns.",
        })


class ScheduleTaskTool(BaseSystemTool):
    """Schedule a task to run at a specific time."""

    @property
    def name(self) -> str:
        return "schedule_task"

    @property
    def description(self) -> str:
        return "Schedule a task to run at a specific time"

    async def execute(self, context: ToolContext) -> ToolResult:
        # This tool requires special handling with user input
        # It's typically executed via execute_schedule_task_inline
        return ToolResult.fail("schedule_task requires user input - use execute_schedule_task_inline")


class CompressConversationTool(BaseSystemTool):
    """Compress conversation history to reduce context window size.

    Summarises older messages via the LLM and replaces them with a compact
    summary message, keeping the most recent messages intact.  Configurable
    via environment variables (see .env.example):

      PROGENITOR_COMPRESS_KEEP_RECENT   – recent messages to preserve (default 10)
      PROGENITOR_COMPRESS_MAX_SUMMARY_TOKENS – target summary length (default 500)
      PROGENITOR_COMPRESS_THRESHOLD     – message count before auto-flag (default 30)
      PROGENITOR_COMPRESS_PERSIST       – write result to current_state.json (default true)
    """

    @property
    def name(self) -> str:
        return "compress_conversation"

    @property
    def description(self) -> str:
        return (
            "Compress conversation history by summarising older messages "
            "to reduce context window usage"
        )

    async def execute(self, context: ToolContext) -> ToolResult:
        from ...llm.conversation_compressor import ConversationCompressor
        from ...state.models import ConversationMessage
        from ...llm.tokenizer import count_message_tokens

        # ── Load config from env with sane defaults ──────────────────────
        keep_recent = env_int("PROGENITOR_COMPRESS_KEEP_RECENT", 10)
        max_summary_tokens = env_int("PROGENITOR_COMPRESS_MAX_SUMMARY_TOKENS", 500)
        threshold = env_int("PROGENITOR_COMPRESS_THRESHOLD", 30)
        persist = env_bool("PROGENITOR_COMPRESS_PERSIST", True)

        # ── Gather messages ──────────────────────────────────────────────
        messages: list[ConversationMessage] = []

        # Prefer state_manager for the persisted source of truth
        if context.state_manager:
            try:
                messages = await context.state_manager.get_messages()
            except Exception as e:
                logger.warning("compress_conversation: failed to read via state_manager: %s", e)

        # Fall back to in-memory state
        if not messages and context.state:
            raw = context.state.get("messages", [])
            for m in raw:
                if isinstance(m, ConversationMessage):
                    messages.append(m)
                elif isinstance(m, dict):
                    try:
                        messages.append(ConversationMessage(**m))
                    except Exception:
                        continue

        if not messages:
            return ToolResult.ok({
                "compressed": False,
                "reason": "No messages found in conversation history.",
                "message_count": 0,
            })

        original_count = len(messages)

        if original_count <= keep_recent:
            return ToolResult.ok({
                "compressed": False,
                "reason": f"Only {original_count} messages — below keep_recent ({keep_recent}). Nothing to compress.",
                "message_count": original_count,
            })

        # ── Token estimate before compression ────────────────────────────
        msg_dicts_before = [{"role": m.role, "content": m.content} for m in messages]
        tokens_before = count_message_tokens(msg_dicts_before)

        # ── Compress ─────────────────────────────────────────────────────
        compressor = ConversationCompressor(
            llm_client=context.llm_client,
            max_summary_tokens=max_summary_tokens,
        )

        try:
            compressed = await compressor.compress(messages, keep_recent=keep_recent)
        except Exception as e:
            logger.error("compress_conversation: compression failed: %s", e, exc_info=True)
            return ToolResult.fail(f"Compression failed: {e}")

        # ── Token estimate after compression ─────────────────────────────
        msg_dicts_after = [{"role": m.role, "content": m.content} for m in compressed]
        tokens_after = count_message_tokens(msg_dicts_after)

        # ── Persist if enabled ───────────────────────────────────────────
        if persist and context.state_manager:
            try:
                await context.state_manager.save_messages(compressed)
                logger.info(
                    "compress_conversation: persisted %d → %d messages (%d → %d tokens)",
                    original_count, len(compressed), tokens_before, tokens_after,
                )
            except Exception as e:
                logger.warning("compress_conversation: failed to persist: %s", e)

        return ToolResult.ok({
            "compressed": True,
            "original_message_count": original_count,
            "compressed_message_count": len(compressed),
            "messages_summarised": original_count - len(compressed) + 1,  # +1 for summary msg
            "tokens_before": tokens_before,
            "tokens_after": tokens_after,
            "tokens_saved": tokens_before - tokens_after,
            "compression_ratio": round(tokens_after / tokens_before, 2) if tokens_before else 1.0,
            "keep_recent": keep_recent,
            "threshold": threshold,
            "persisted": persist and context.state_manager is not None,
        })
