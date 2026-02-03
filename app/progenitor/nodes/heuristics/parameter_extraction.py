"""
Parameter Extraction Functions.

Extract structured parameters from user input for various operations.
"""

import re
from typing import Any


def extract_schedule_params(user_input: str) -> dict[str, Any]:
    """Extract schedule parameters from user input."""
    text = user_input.lower()

    # Try to extract time expression
    time_patterns = [
        r'(every day at \d{1,2}(?::\d{2})?\s*(?:am|pm)?)',
        r'(at \d{1,2}(?::\d{2})?\s*(?:am|pm)?)',
        r'(in \d+ (?:minute|hour|day)s?)',
        r'(tomorrow(?: at \d{1,2}(?::\d{2})?\s*(?:am|pm)?)?)',
        r'(every (?:morning|evening|hour|day|week))',
    ]

    time_expression = ""
    for pattern in time_patterns:
        match = re.search(pattern, text)
        if match:
            time_expression = match.group(1)
            break

    return {
        "user_request": user_input,
        "time_expression": time_expression,
    }


def infer_target_capabilities(user_input: str) -> list[str]:
    """Infer a likely capability name from the user's request."""
    text = (user_input or "").lower()

    if "hello world" in text or "hello-world" in text:
        return ["hello_world_a2a_agent"]

    # Try to extract an agent name: "build/create <name> agent"
    match = re.search(r"\b(?:build|create|scaffold|generate|make|implement)\s+(?:an?\s+)?([a-z0-9 _\-]{1,64})\s+agent\b", text)
    if not match:
        return []

    raw_name = match.group(1).strip()
    # Convert to snake_case-ish.
    slug = re.sub(r"[^a-z0-9]+", "_", raw_name).strip("_")
    if not slug:
        return []

    return [f"{slug}_a2a_agent"]


def extract_project_name_from_input(user_input: str) -> str | None:
    """Extract project/agent name from user input.

    Returns None if no name can be extracted.
    """
    text = user_input.lower()

    # Pattern: "build/create [a] <name> agent/mcp"
    name_patterns = [
        r'\b(?:build|create|scaffold|generate|make|implement)\s+(?:me\s+)?(?:a\s+|an\s+)?(?:new\s+)?([a-z0-9 _\-]+?)\s+(?:a2a\s+)?(?:agent|mcp)',
        r'\b(?:named?|called?)\s+([a-z0-9 _\-]+)',
        r'\bagent\s+(?:named?|called?)\s+([a-z0-9 _\-]+)',
    ]

    for pattern in name_patterns:
        match = re.search(pattern, text)
        if match:
            raw_name = match.group(1).strip()
            # Filter out common words that aren't names
            if raw_name and raw_name not in ("a", "an", "the", "new", "simple", "basic"):
                return raw_name

    return None


def extract_meaningful_words(user_input: str) -> list[str]:
    """Extract meaningful words from user input (excluding common verbs/articles)."""
    text = user_input.lower()
    exclude_words = {'build', 'create', 'make', 'an', 'a', 'the', 'me', 'agent', 'mcp', 'new', 'please', 'can', 'you'}
    words = re.findall(r'[a-z]+', text)
    return [w for w in words if w not in exclude_words]
