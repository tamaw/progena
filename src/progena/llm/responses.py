"""Pure responses helpers for auxiliary model calls."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)


INLINE_TOOL_MARKUP_PATTERN = re.compile(r".*?", re.DOTALL)


def extract_text_and_reasoning(
    data: dict[str, Any], sanitize: Callable[[str], str]
) -> tuple[str, str]:
    """Extract response text and optional reasoning trace from /responses output."""
    text_parts: list[str] = []
    reasoning_parts: list[str] = []

    def _append_text_candidate(candidate: Any) -> None:
        if candidate is None:
            return
        if isinstance(candidate, str):
            if candidate.strip():
                text_parts.append(candidate)
            return
        if isinstance(candidate, list):
            for item in candidate:
                _append_text_candidate(item)
            return
        if isinstance(candidate, dict):
            for key in (
                "text",
                "value",
                "content",
                "output_text",
                "refusal",
                "message",
                "parts",
            ):
                if key in candidate:
                    _append_text_candidate(candidate.get(key))

    # Primary text response
    if output_text := data.get("output_text"):
        _append_text_candidate(output_text)

    # Some providers return final text in top-level `text`
    _append_text_candidate(data.get("text"))

    # Structured output items
    for item in data.get("output", []):
        item_type = item.get("type")

        if item_type == "message":
            content = item.get("content", "")
            if isinstance(content, (str, dict)):
                _append_text_candidate(content)
            elif isinstance(content, list):
                for part in content:
                    if not isinstance(part, dict):
                        continue
                    part_type = part.get("type")
                    if part_type in ("output_text", "text"):
                        _append_text_candidate(part.get("text"))
                        _append_text_candidate(part.get("value"))
                        _append_text_candidate(part.get("content"))
                    elif part_type == "refusal":
                        _append_text_candidate(part.get("refusal") or part.get("text"))
                    elif part_type in ("reasoning", "reasoning_text", "reasoning_summary"):
                        reasoning_parts.append(part.get("text", ""))

        elif item_type in ("output_text", "text"):
            _append_text_candidate(item.get("text"))
            _append_text_candidate(item.get("value"))
            _append_text_candidate(item.get("content"))

        elif item_type in ("reasoning", "reasoning_text", "reasoning_summary"):
            reasoning_parts.append(str(item.get("content") or item.get("text", "")))

        elif item_type == "refusal":
            _append_text_candidate(item.get("refusal") or item.get("text"))

    # Top-level reasoning
    if reasoning := data.get("reasoning"):
        reasoning_parts.append(str(reasoning))

    text = sanitize("".join(text_parts).strip())
    reasoning = "".join(reasoning_parts).strip()

    if not text:
        error_payload = data.get("error")
        status = data.get("status")
        incomplete_details = data.get("incomplete_details")
        truncation = data.get("truncation")
        top_text = data.get("text")
        output_value = data.get("output")
        output_items: list[Any] = output_value if isinstance(output_value, list) else []

        output_summaries: list[dict[str, Any]] = []
        for item in output_items[:5]:
            if not isinstance(item, dict):
                output_summaries.append({"type": type(item).__name__})
                continue
            content = item.get("content")
            content_shape: Any
            if isinstance(content, list):
                content_shape = [
                    {
                        "type": c.get("type") if isinstance(c, dict) else type(c).__name__,
                        "keys": list(c.keys())[:8] if isinstance(c, dict) else None,
                    }
                    for c in content[:5]
                ]
            elif isinstance(content, dict):
                content_shape = {"keys": list(content.keys())[:12]}
            else:
                content_shape = type(content).__name__

            output_summaries.append(
                {
                    "type": item.get("type"),
                    "role": item.get("role"),
                    "status": item.get("status"),
                    "keys": list(item.keys())[:12],
                    "content_shape": content_shape,
                }
            )

        if error_payload:
            logger.warning(
                "Empty LLM response with provider error - status=%s error=%s incomplete_details=%s truncation=%s keys=%s top_text=%r output_summaries=%s",
                status,
                error_payload,
                incomplete_details,
                truncation,
                list(data.keys()),
                top_text,
                output_summaries,
            )
        else:
            logger.info(
                "Empty LLM response without provider error - status=%s incomplete_details=%s truncation=%s keys=%s top_text=%r output_summaries=%s",
                status,
                incomplete_details,
                truncation,
                list(data.keys()),
                top_text,
                output_summaries,
            )

    return text, reasoning


def sanitize_user_visible_text(text: str) -> str:
    """Remove provider/tool markup artifacts from user-visible text."""
    if not text:
        return text

    # Some providers emit inline tool-call traces (e.g.
    # "tool_name{...json...}") as plain text. Strip these blocks so
    # user-facing responses remain clean.
    cleaned = INLINE_TOOL_MARKUP_PATTERN.sub("", text)

    # Normalize accidental leading blank lines introduced by stripping.
    return cleaned.lstrip()
