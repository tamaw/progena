"""
Intent Detection Heuristics.

Fast-path heuristics that detect specific user intents without LLM calls.
These are designed to be very specific to avoid false positives.
"""

import re
from typing import Optional


def looks_like_agent_build_request(user_input: str) -> bool:
    """Heuristic: detect explicit requests to build/create a new agent.

    This should only trigger for very explicit agent creation requests.
    The user must clearly express intent to BUILD/CREATE an AGENT.
    Questions about agents, general mentions, or ambiguous requests should NOT trigger this.
    """
    text = (user_input or "").lower()
    if not text.strip():
        return False

    # Explicit combined phrases that clearly indicate agent building intent
    # These are very specific to avoid false positives
    explicit_build_phrases = [
        "build an agent",
        "build a new agent",
        "build me an agent",
        "build an a2a agent",
        "build a2a agent",
        "build me an a2a agent",
        "create an agent",
        "create a new agent",
        "create me an agent",
        "create an a2a agent",
        "create a2a agent",
        "create me an a2a agent",
        "scaffold an agent",
        "scaffold a new agent",
        "scaffold an a2a agent",
        "generate an agent",
        "generate a new agent",
        "generate an a2a agent",
        "make an agent",
        "make a new agent",
        "make me an agent",
        "make an a2a agent",
        "implement an agent",
        "implement a new agent",
        "implement an a2a agent",
        # With specific agent names
        "build a hello world agent",
        "create a hello world agent",
        "build a weather agent",
        "create a weather agent",
    ]

    # Check for explicit phrases first
    if any(phrase in text for phrase in explicit_build_phrases):
        return True

    # Fallback: require BOTH strong build verb AND agent noun in close proximity
    # This is more strict than before

    # Pattern: build/create/scaffold + optional words + agent (but not just "agent" alone)
    build_agent_pattern = r'\b(build|create|scaffold|generate|implement)\s+(?:me\s+)?(?:a\s+|an\s+|the\s+)?(?:new\s+)?(?:[a-z]+\s+){0,3}agent\b'

    if re.search(build_agent_pattern, text):
        # Exclude false positives
        false_positives = [
            "build a plan",
            "build the plan",
            "build a docker",
            "build an image",
            "build the image",
            "build a container",
            "build the container",
            "what agent",
            "which agent",
            "about agent",
            "the agent",  # Referring to existing agent
            "your agent",
            "this agent",
        ]
        if any(fp in text for fp in false_positives):
            return False
        return True

    return False


def looks_like_agent_discovery_request(user_input: str) -> bool:
    """Heuristic: detect requests to rediscover/rewire agents."""
    text = (user_input or "").lower()
    if not text.strip():
        return False

    # Direct compound phrases that clearly indicate discovery intent
    direct_discovery_phrases = (
        "rewire agents",
        "rewire the agents",
        "rediscover agents",
        "rescan agents",
        "refresh agents",
        "reload agents",
        "scan agents",
        "discover agents",
        "update registry",
        "refresh registry",
        "sync agents",
        "re-scan agents",
        "re-discover agents",
        # Key pattern: "agent discovery" as a compound noun
        "agent discovery",
        "run agent discovery",
        "run discovery",
        "do agent discovery",
        "start agent discovery",
        "perform agent discovery",
        "trigger agent discovery",
        "execute agent discovery",
    )

    if any(phrase in text for phrase in direct_discovery_phrases):
        return True

    # Single-word discovery verbs that imply agents when "agent" is present
    has_discovery_verb = any(
        verb in text
        for verb in (
            "rewire",
            "rediscover",
            "rescan",
            "re-scan",
            "re-discover",
            "refresh",
            "reload",
        )
    )

    has_agent_context = any(
        phrase in text
        for phrase in (
            "agent",
            "registry",
        )
    )

    return has_discovery_verb and has_agent_context


def looks_like_list_tools_request(user_input: str) -> bool:
    """Heuristic: detect requests to list available tools/capabilities."""
    text = (user_input or "").lower()
    if not text.strip():
        return False

    has_list_intent = any(
        phrase in text
        for phrase in (
            "list tool",
            "list all tool",
            "show tool",
            "show all tool",
            "what tool",
            "available tool",
            "list capabilit",
            "show capabilit",
            "what can you do",
            "your capabilit",
            "all available",
        )
    )

    has_tool_context = any(
        phrase in text
        for phrase in (
            "tool",
            "capabilit",
            "function",
        )
    )

    # Direct patterns
    direct_patterns = [
        "list tools",
        "show tools",
        "list your tools",
        "show your tools",
        "what tools",
        "which tools",
        "available tools",
    ]

    return has_list_intent or any(pattern in text for pattern in direct_patterns)


def looks_like_build_status_request(user_input: str) -> bool:
    """Heuristic: detect requests to check build status/progress."""
    text = (user_input or "").lower()
    if not text.strip():
        return False

    # Direct patterns for build status
    direct_patterns = [
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

    return any(pattern in text for pattern in direct_patterns)


def looks_like_current_time_request(user_input: str) -> bool:
    """Heuristic: detect requests for the current time (UTC)."""
    text = (user_input or "").lower()
    if not text.strip():
        return False

    direct_patterns = [
        "current time",
        "utc time",
        "time now",
        "what time is it",
        "tell me the time",
        "tell me the current time",
        "what is the time",
        "whats the time",
        "what's the time",
    ]

    return any(pattern in text for pattern in direct_patterns)


def looks_like_schedule_request(user_input: str) -> bool:
    """Heuristic: detect requests to schedule/remind/do something at a time."""
    text = (user_input or "").lower()
    if not text.strip():
        return False

    # Time indicators
    has_time_indicator = any(
        phrase in text
        for phrase in (
            " at ",
            "every day",
            "every morning",
            "every evening",
            "every hour",
            "every minute",
            "in the morning",
            "in the evening",
            "tomorrow",
            "next week",
            "in 5 minutes",
            "in 10 minutes",
            "in 15 minutes",
            "in 30 minutes",
            "in an hour",
            "in 1 hour",
            "in 2 hours",
            "daily",
            "weekly",
        )
    )

    # Schedule/reminder intent
    has_schedule_intent = any(
        phrase in text
        for phrase in (
            "remind",
            "schedule",
            "set a reminder",
            "notify me",
            "tell me at",
            "say to me at",
            "alert me",
            "wake me",
        )
    )

    # Time patterns like "9am", "10:30", "3pm"
    has_time_pattern = bool(re.search(r'\d{1,2}(?::\d{2})?\s*(?:am|pm)', text))

    return (has_time_indicator and has_time_pattern) or has_schedule_intent


def looks_like_list_schedule_request(user_input: str) -> bool:
    """Heuristic: detect requests to list/view scheduled tasks."""
    text = (user_input or "").lower()
    if not text.strip():
        return False

    # Direct patterns for listing schedule
    direct_patterns = [
        "what is your schedule",
        "what's your schedule",
        "whats your schedule",
        "show schedule",
        "show my schedule",
        "show the schedule",
        "list schedule",
        "list schedules",
        "list scheduled",
        "view schedule",
        "view schedules",
        "scheduled tasks",
        "what is scheduled",
        "what's scheduled",
        "whats scheduled",
        "any scheduled",
        "pending schedule",
        "upcoming tasks",
        "upcoming schedule",
        "my reminders",
        "show reminders",
        "list reminders",
        "what reminders",
    ]

    return any(pattern in text for pattern in direct_patterns)


def looks_like_self_reflect_request(user_input: str) -> bool:
    """Heuristic: detect requests for self-reflection/introspection."""
    text = (user_input or "").lower()
    if not text.strip():
        return False

    # Direct self-reflection patterns
    direct_patterns = [
        "how do you work",
        "how do you handle",
        "how do you process",
        "what is your",
        "what are your",
        "explain your",
        "describe your",
        "your source code",
        "your implementation",
        "your architecture",
        "how are you built",
        "how were you built",
        "how are you implemented",
        "self reflect",
        "self-reflect",
        "introspect",
        "understand yourself",
        "examine yourself",
        "look at your code",
        "read your code",
        "your own code",
        "your capabilities",
        "what can you do",
    ]

    # Check for self-referential questions about internals
    has_self_reference = any(
        phrase in text
        for phrase in (
            "yourself",
            "your code",
            "your source",
            "your implementation",
            "your internal",
            "how you ",
            "what you ",
        )
    )

    has_introspection_intent = any(
        phrase in text
        for phrase in (
            "work",
            "function",
            "implement",
            "process",
            "handle",
            "built",
            "design",
            "architecture",
        )
    )

    return any(pattern in text for pattern in direct_patterns) or (
        has_self_reference and has_introspection_intent
    )
