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

    # Try to extract an agent name with improved pattern
    # Skip filler words like "for me", "please", etc.
    match = re.search(
        r"\b(?:build|create|scaffold|generate|make|implement)\s+"
        r"(?:for\s+me\s+)?(?:me\s+)?(?:please\s+)?"
        r"(?:an?\s+)?(?:new\s+)?(?:simple\s+|basic\s+)?"
        r"([a-z][a-z0-9]*(?:\s+[a-z][a-z0-9]*)?)\s+agent\b",
        text
    )
    if not match:
        return []

    raw_name = match.group(1).strip()
    # Convert to snake_case
    slug = re.sub(r"[^a-z0-9]+", "_", raw_name).strip("_")
    if not slug:
        return []

    return [f"{slug}_a2a_agent"]


def extract_project_name_from_input(user_input: str) -> str | None:
    """Extract project/agent name from user input.

    Returns None if no name can be extracted.
    """
    text = user_input.lower()

    # Improved patterns that skip filler words like "for me", "please"
    name_patterns = [
        # "build/create [for me] [a] <name> agent" - handles "build for me a weather agent"
        r'\b(?:build|create|scaffold|generate|make|implement)\s+(?:for\s+me\s+)?(?:me\s+)?(?:please\s+)?(?:a\s+|an\s+)?(?:new\s+)?(?:simple\s+|basic\s+)?([a-z][a-z0-9]*(?:\s+[a-z][a-z0-9]*)?)\s+(?:a2a\s+)?(?:agent|mcp)',
        # "agent named/called <name>"
        r'\bagent\s+(?:named?|called?)\s+([a-z0-9_\-]+)',
        # "named/called <name>"
        r'\b(?:named?|called?)\s+([a-z0-9_\-]+)',
    ]

    # Words that should never be part of the project name
    skip_words = {"a", "an", "the", "new", "simple", "basic", "for", "me", "please"}

    for pattern in name_patterns:
        match = re.search(pattern, text)
        if match:
            raw_name = match.group(1).strip()
            # Filter out filler words
            if raw_name and raw_name not in skip_words:
                # Convert spaces to underscores for snake_case
                return re.sub(r'\s+', '_', raw_name)

    return None


def extract_meaningful_words(user_input: str) -> list[str]:
    """Extract meaningful words from user input (excluding common verbs/articles)."""
    text = user_input.lower()
    exclude_words = {
        'build', 'create', 'make', 'scaffold', 'generate', 'implement',
        'an', 'a', 'the', 'me', 'for', 'please', 'new', 'simple', 'basic',
        'agent', 'mcp', 'server', 'tool', 'want', 'need', 'would', 'like',
        'can', 'you', 'i', 'that', 'which', 'to', 'with'
    }
    words = re.findall(r'[a-z]+', text)
    return [w for w in words if w not in exclude_words and len(w) > 2]
