"""
Result Formatters for System Tools.

Format tool results as human-readable responses.
"""

import json
from typing import Any

from ...llm.prompts import PromptTemplates

_prompts = PromptTemplates()


def format_tool_result(tool_name: str, result: dict[str, Any]) -> str:
    """Format a tool result as a human-readable response."""

    if tool_name == "list_tools":
        return _format_list_tools(result)
    elif tool_name == "rediscover_agents":
        return _format_rediscover_agents(result)
    elif tool_name == "list_agents":
        return _format_list_agents(result)
    elif tool_name == "build_status":
        return _format_build_status(result)
    elif tool_name == "self_reflect":
        return _format_self_reflect(result)
    elif tool_name == "current_time":
        return _format_current_time(result)
    elif tool_name == "list_schedule":
        return _format_list_schedule(result)
    elif tool_name == "new_conversation":
        return _format_new_conversation(result)
    elif tool_name == "compress_conversation":
        return _format_compress_conversation(result)
    elif tool_name == "memory":
        return _format_memory(result)
    elif tool_name == "schedule_task":
        return _format_schedule_task(result)
    else:
        # Default: return JSON-like representation
        return f"Result:\n{json.dumps(result, indent=2)}"


def _format_schedule_task(result: dict[str, Any]) -> str:
    """Format schedule_task result."""
    if not result.get("success"):
        message = result.get("message") or result.get("error") or "Unable to schedule the task."
        return f"Could not schedule that reminder: {message}"

    description = (result.get("description") or "Reminder").strip()
    next_run = (result.get("next_run") or "").strip()
    recurring = bool(result.get("recurring", False))

    if recurring and next_run:
        return f"✅ Scheduled: {description}\nNext run: {next_run} (recurring)."
    if recurring:
        return f"✅ Scheduled: {description} (recurring)."
    if next_run:
        return f"✅ Scheduled: {description}\nRun time: {next_run}."
    return f"✅ Scheduled: {description}."


def format_schedule_result(result: dict[str, Any]) -> str:
    """Backward-compatible wrapper for schedule task formatting."""
    return _format_schedule_task(result)


def _format_list_tools(result: dict[str, Any]) -> str:
    """Format list_tools result."""
    lines = ["Available Tools:\n"]

    system_tools = result.get("system_tools", [])
    if system_tools:
        lines.append("System Tools:")
        for tool in system_tools:
            lines.append(f"  - {tool['name']} - {tool['description']}")

    agents = result.get("agents", [])
    if agents:
        lines.append("\nA2A Agents:")
        for agent in agents:
            name = agent.get("name", "unknown")
            desc = agent.get("description", "No description")
            status = agent.get("status", "unknown")
            lines.append(f"  - {name} [{status}] - {desc}")

    mcp_tools = result.get("mcp_tools", [])
    if mcp_tools:
        lines.append("\nMCP Tools:")
        for tool in mcp_tools:
            lines.append(f"  - {tool['name']} - {tool.get('description', 'No description')}")

    lines.append(f"\nTotal: {result.get('total_count', 0)} tools available")
    return "\n".join(lines)


def _format_rediscover_agents(result: dict[str, Any]) -> str:
    """Format rediscover_agents result."""
    found = result.get("agents_found", 0)
    registered = result.get("agents_registered", 0)
    skipped = result.get("agents_skipped", 0)
    errors = result.get("errors", [])

    lines = ["Agent Discovery Complete:"]
    lines.append(f"  - Found: {found} agents")
    lines.append(f"  - Registered: {registered}")
    lines.append(f"  - Skipped: {skipped}")
    if errors:
        lines.append(f"  - Errors: {', '.join(errors)}")
    return "\n".join(lines)


def _format_list_agents(result: dict[str, Any]) -> str:
    """Format list_agents result."""
    agents = result.get("agents", [])
    if not agents:
        return "No agents registered. Use rediscover agents to scan for agents."

    lines = ["Registered Agents:"]
    for agent in agents:
        status = agent.get("status", "unknown")
        endpoint = agent.get("endpoint", "N/A")
        lines.append(f"  - {agent['name']} [{status}]")
        lines.append(f"    - {agent.get('description', 'No description')}")
        lines.append(f"    - Endpoint: {endpoint}")

    lines.append(f"\nRunning: {result.get('running_count', 0)}/{result.get('total_count', 0)}")
    return "\n".join(lines)


def _format_build_status(result: dict[str, Any]) -> str:
    """Format build_status result."""
    build_in_progress = result.get("build_in_progress", False)
    status = result.get("status", "unknown")

    # Handle completed/failed builds
    if status in ("completed", "failed", "cancelled"):
        status_emoji = "✓" if status == "completed" else "✗" if status == "failed" else "○"
        lines = [f"{status_emoji} Build {status.title()}"]
        lines.append(f"  - Task: {result.get('task_description', 'No description')}")
        lines.append(f"  - Build ID: {result.get('build_id', 'unknown')}")
        lines.append(f"  - Started: {result.get('started_at', 'unknown')}")
        lines.append(f"  - Completed: {result.get('completed_at', 'unknown')}")
        lines.append(f"  - Iterations: {result.get('iterations', 0)}")
        lines.append(f"  - Final Score: {result.get('verification_score', 0):.2f}")

        if result.get("agent_name"):
            lines.append(f"  - Agent Built: {result['agent_name']}")

        if result.get("output_summary"):
            lines.append(f"\nSummary: {result['output_summary']}")

        if result.get("error_message"):
            lines.append(f"\nError: {result['error_message']}")

        # Show criteria results
        met = result.get("success_criteria_met", [])
        unmet = result.get("success_criteria_unmet", [])
        if met:
            lines.append(f"\n✓ Criteria Met ({len(met)}):")
            for c in met[:5]:
                lines.append(f"  - {c}")
        if unmet:
            lines.append(f"\n○ Criteria Unmet ({len(unmet)}):")
            for c in unmet[:5]:
                lines.append(f"  - {c}")

        return "\n".join(lines)

    if not build_in_progress:
        # Check for message indicating no builds
        message = result.get("message")
        if message:
            return f"📋 Build Status\n\n{message}"
        return "No build in progress. The system is idle and ready for new build requests."

    # Running build
    lines = ["🔨 Build In Progress"]
    lines.append(f"  - Task: {result.get('task_description', 'No description')}")
    lines.append(f"  - Build ID: {result.get('build_id', 'unknown')}")
    lines.append(f"  - Status: {status}")
    lines.append(f"  - Started: {result.get('started_at', 'unknown')}")
    lines.append(f"  - Iterations: {result.get('iterations', 0)}")
    lines.append(f"  - Current Score: {result.get('verification_score', 0):.2f}")

    # Show phase if available (sync builds)
    phase = result.get("phase")
    if phase:
        lines.append(f"  - Phase: {phase}")

    criteria = result.get("success_criteria", [])
    if criteria:
        lines.append("\nSuccess Criteria:")
        for c in criteria:
            lines.append(f"  - {c}")

    recent = result.get("recent_messages", [])
    if recent:
        lines.append(f"\nRecent Activity ({result.get('message_count', 0)} total messages):")
        for msg in recent[-3:]:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")[:100]
            if len(msg.get("content", "")) > 100:
                content += "..."
            lines.append(f"  [{role}] {content}")

    return "\n".join(lines)


def _format_self_reflect(result: dict[str, Any]) -> str:
    """Format self_reflect result."""
    modules = result.get("modules", [])
    dirs = [m for m in modules if m.get("is_directory")]
    files = [m for m in modules if not m.get("is_directory")]

    lines = ["🔍 Progenitor Self-Reflection\n"]
    lines.append(f"Source location: {result.get('root_path', 'unknown')}\n")

    if dirs:
        lines.append("Modules (directories):")
        for d in dirs:
            lines.append(f"  - {d['name']}/")

    if files:
        lines.append("\nRoot files:")
        for f in files:
            desc = f" - {f['description']}" if f.get('description') else ""
            lines.append(f"  - {f['name']}{desc}")

    lines.append(f"\nSummary: {result.get('module_count', 0)} modules, {result.get('file_count', 0)} root files")

    tip = result.get("tip")
    if tip:
        lines.append(f"\n💡 {tip}")

    return "\n".join(lines)


def _format_current_time(result: dict[str, Any]) -> str:
    """Format current_time result."""
    utc_time = result.get("utc_time")
    if not utc_time:
        return "Unable to determine current UTC time."
    return f"Current UTC time: {utc_time}"


def _format_list_schedule(result: dict[str, Any]) -> str:
    """Format list_schedule result."""
    tasks = result.get("scheduled_tasks", [])

    if not tasks:
        return (
            "📅 No Scheduled Tasks\n\n"
            "You don't have any scheduled tasks or reminders set up.\n\n"
            "💡 You can schedule tasks by saying things like 'remind me at 9am to check emails' "
            "or 'every day at 5pm run a status check'."
        )

    lines = ["📅 Scheduled Tasks:\n"]
    for task in tasks:
        enabled = "✓" if task.get("enabled", True) else "○"
        lines.append(f"  {enabled} {task.get('description', 'Unnamed task')}")
        if task.get("cron_expression"):
            lines.append(f"      Schedule: {task['cron_expression']}")
        if task.get("next_run"):
            lines.append(f"      Next run: {task['next_run']}")
        if task.get("action_type"):
            lines.append(f"      Action: {task['action_type']}")
        lines.append("")  # Blank line between tasks

    lines.append(f"Total: {result.get('total_count', 0)} scheduled task(s)")
    return "\n".join(lines)


def _format_new_conversation(result: dict[str, Any]) -> str:
    """Format new_conversation result."""
    new_run_id = result.get("new_run_id", "unknown")
    previous_run_id = result.get("previous_run_id", "unknown")
    messages_cleared = result.get("messages_cleared", 0)

    lines = ["🔄 New Conversation Started"]
    lines.append(f"  - New Run ID: {new_run_id}")
    if previous_run_id and previous_run_id != "unknown":
        lines.append(f"  - Previous Run ID: {previous_run_id}")
    if messages_cleared:
        lines.append(f"  - Messages cleared: {messages_cleared}")
    lines.append("\nFresh conversation ready. How can I help?")
    return "\n".join(lines)


def _format_compress_conversation(result: dict[str, Any]) -> str:
    """Format compress_conversation result."""
    original = result.get("original_message_count", 0)
    compressed = result.get("compressed_message_count", 0)
    summary_length = result.get("summary_length", 0)

    lines = ["📦 Conversation Compressed"]
    lines.append(f"  - Before: {original} messages")
    lines.append(f"  - After: {compressed} messages")
    if summary_length:
        lines.append(f"  - Summary: {summary_length} characters")
    return "\n".join(lines)


def _format_memory(result: dict[str, Any]) -> str:
    """Format memory tool results."""
    action = result.get("action", "unknown")

    if action == "store":
        entry = result.get("entry", {})
        summary = entry.get("summary", "(no summary)")
        entry_id = entry.get("id", "unknown")
        return "\n".join([
            "🧠 Memory Stored",
            f"  - ID: {entry_id}",
            f"  - Summary: {summary}",
        ])

    if action == "list":
        entries = result.get("entries", [])
        if not entries:
            return "🧠 Memories\n\nNo memories found."

        lines = ["🧠 Memories"]
        for entry in entries:
            entry_id = entry.get("id", "unknown")
            summary = entry.get("summary", "(no summary)")
            created_at = entry.get("created_at", "unknown")
            lines.append(f"  - {entry_id} - {summary} ({created_at})")
        total = result.get("total_count")
        if total is not None:
            lines.append(f"\nTotal: {total} memory entries")
        return "\n".join(lines)

    if action == "search":
        entries = result.get("entries", [])
        if not entries:
            return "🧠 Memory Search\n\nNo matching memories found."

        lines = ["🧠 Memory Search Results"]
        for entry in entries:
            entry_id = entry.get("id", "unknown")
            summary = entry.get("summary", "(no summary)")
            created_at = entry.get("created_at", "unknown")
            lines.append(f"  - {entry_id} - {summary} ({created_at})")
        lines.append(f"\nMatches: {len(entries)}")
        return "\n".join(lines)

    if action == "delete":
        deleted = result.get("deleted_count", 0)
        return f"🧠 Memory Deleted\n\nDeleted {deleted} entr{'y' if deleted == 1 else 'ies'}."

    if action == "clear":
        cleared = result.get("cleared_count", 0)
        return f"🧠 Memory Cleared\n\nDeleted {cleared} entr{'y' if cleared == 1 else 'ies'}."

    if action == "skip":
        reason = result.get("reason", "No durable memory to store.")
        return f"🧠 Memory Skipped\n\n{reason}"

    return f"🧠 Memory\n\n{json.dumps(result, indent=2)}"
