"""Responses API message conversion and text extraction."""

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

INLINE_TOOL_MARKUP_PATTERN = re.compile(r".*?", re.DOTALL)


def build_input_messages(
    messages: list[dict[str, Any]],
    system_prompt: str | None = None,
) -> list[dict[str, Any]]:
    """Build Responses API input messages from chat history."""
    input_messages: list[dict[str, Any]] = []
    allowed_roles = {"system", "user", "assistant", "tool"}

    if system_prompt:
        input_messages.append(
            {
                "role": "system",
                "content": system_prompt,
            }
        )

    for msg in messages:
        role = str(msg.get("role", "user") or "user")
        if role not in allowed_roles:
            role = "user"
        content = msg.get("content", "")

        if isinstance(content, list):
            if (
                content
                and isinstance(content[0], dict)
                and content[0].get("type")
                in {
                    "input_text",
                    "output_text",
                    "input_image",
                    "input_file",
                    "refusal",
                    "summary_text",
                    "tether_browsing_display",
                    "computer_screenshot",
                    "tool_call",
                    "tool_result",
                }
            ):
                wrapped_content: Any = content
            else:
                text_parts = []
                for part in content:
                    if isinstance(part, dict):
                        text_parts.append(str(part.get("text", "")))
                    elif isinstance(part, str):
                        text_parts.append(part)
                wrapped_content = "".join(text_parts)
        else:
            wrapped_content = str(content)

        input_messages.append(
            {
                "role": role,
                "content": wrapped_content,
            }
        )

    return input_messages


def sanitize_user_visible_text(text: str) -> str:
    """Remove provider/tool markup artifacts from user-visible text."""
    if not text:
        return text
    return INLINE_TOOL_MARKUP_PATTERN.sub("", text).lstrip()


def extract_text_and_reasoning(data: dict[str, Any]) -> tuple[str, str]:
    """Extract response text and optional reasoning trace from /responses output."""
    text_parts: list[str] = []
    reasoning_parts: list[str] = []

    def append_text_candidate(candidate: Any) -> None:
        if candidate is None:
            return
        if isinstance(candidate, str):
            if candidate.strip():
                text_parts.append(candidate)
            return
        if isinstance(candidate, list):
            for item in candidate:
                append_text_candidate(item)
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
                    append_text_candidate(candidate.get(key))

    if output_text := data.get("output_text"):
        append_text_candidate(output_text)

    append_text_candidate(data.get("text"))

    for item in data.get("output", []):
        item_type = item.get("type")

        if item_type == "message":
            content = item.get("content", "")
            if isinstance(content, (str, dict)):
                append_text_candidate(content)
            elif isinstance(content, list):
                for part in content:
                    if not isinstance(part, dict):
                        continue
                    part_type = part.get("type")
                    if part_type in ("output_text", "text"):
                        append_text_candidate(part.get("text"))
                        append_text_candidate(part.get("value"))
                        append_text_candidate(part.get("content"))
                    elif part_type == "refusal":
                        append_text_candidate(part.get("refusal") or part.get("text"))
                    elif part_type in ("reasoning", "reasoning_text", "reasoning_summary"):
                        reasoning_parts.append(part.get("text", ""))

        elif item_type in ("output_text", "text"):
            append_text_candidate(item.get("text"))
            append_text_candidate(item.get("value"))
            append_text_candidate(item.get("content"))

        elif item_type in ("reasoning", "reasoning_text", "reasoning_summary"):
            reasoning_parts.append(str(item.get("content") or item.get("text", "")))

        elif item_type == "refusal":
            append_text_candidate(item.get("refusal") or item.get("text"))

    if reasoning := data.get("reasoning"):
        reasoning_parts.append(str(reasoning))

    text = sanitize_user_visible_text("".join(text_parts).strip())
    reasoning = "".join(reasoning_parts).strip()

    if not text:
        log_empty_response_shape(data)

    return text, reasoning


def log_empty_response_shape(data: dict[str, Any]) -> None:
    """Log a compact response shape when provider output has no user-visible text."""
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
