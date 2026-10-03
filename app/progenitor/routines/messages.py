"""Normalize routine message text and preserve model response metadata."""

from __future__ import annotations

import json
import re
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage

_FENCED_JSON_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def normalize_message_content(content: Any) -> str:
    """Reduce LangChain message content (str, or Responses-API block list) to text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return "".join(parts)
    return str(content)


def _message_with_usage(text: str, source: BaseMessage | None) -> AIMessage:
    """A fresh AIMessage carrying `text` (already normalized off `source`,
    which may be a Responses-API block list `source.content` isn't) --
    preserves `source`'s usage_metadata rather than dropping it, so
    _instrument_node_callable's uniform token-usage extraction (reads
    `messages`, see factory.py) still sees a real LLM call's usage even
    though this node reconstructs the message instead of returning `source`
    verbatim."""
    return AIMessage(content=text, usage_metadata=getattr(source, "usage_metadata", None))


def extract_json_object(text: str) -> dict[str, Any] | None:
    """Best-effort JSON-object extraction: fenced block, then a balanced-brace scan."""
    candidates: list[str] = []
    fenced = _FENCED_JSON_RE.search(text)
    if fenced:
        candidates.append(fenced.group(1))
    candidates.append(text.strip())
    brace_substring = _balanced_brace_substring(text)
    if brace_substring:
        candidates.append(brace_substring)

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _balanced_brace_substring(text: str) -> str | None:
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None
