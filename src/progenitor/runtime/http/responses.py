"""Pure conversion helpers for the OpenAI Responses/Conversations API contract.

Contains no I/O or HTTP logic — only data transformation, event builders,
and message→item mapping.
"""

from __future__ import annotations

import json
import time
from typing import Any

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
    messages_to_dict,
)

from src.progenitor.sessions.status import CANCELLED_STATUS


def error_response(
    message: str,
    code: str,
    status: int,
    type_: str = "invalid_request_error",
    param: str | None = None,
) -> tuple[dict[str, Any], int]:
    """Build an error payload and HTTP status pair."""
    payload: dict[str, Any] = {
        "error": {
            "message": message,
            "type": type_,
            "code": code,
            "param": param,
        },
    }
    return payload, status


# ``response_metadata["status"]`` of an assistant message saved from a
# cancelled turn (its partial reply); its items get Responses status "incomplete".


def _unique_item_id(base: str, seen: set[str]) -> str:
    if base not in seen:
        seen.add(base)
        return base
    n = 1
    while f"{base}:{n}" in seen:
        n += 1
    candidate = f"{base}:{n}"
    seen.add(candidate)
    return candidate


def _human_content_parts(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return [{"type": "input_text", "text": content}]
    if isinstance(content, list):
        parts: list[dict[str, Any]] = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "input_text":
                    parts.append({"type": "input_text", "text": str(block.get("text", ""))})
                else:
                    parts.append(dict(block))
            else:
                parts.append({"type": "input_text", "text": str(block)})
        return parts
    return [{"type": "input_text", "text": str(content)}]


def _ai_text_block_to_part(block: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "output_text",
        "text": str(block.get("text", "")),
        "annotations": block.get("annotations", []),
    }


def _function_call_items(msg: AIMessage, base_id: str, seen_ids: set[str]) -> list[dict[str, Any]]:
    """``function_call`` items for an AIMessage's normalized ``tool_calls``."""
    return [
        {
            "type": "function_call",
            "id": _unique_item_id(tool_call.get("id") or f"{base_id}:fc", seen_ids),
            "call_id": tool_call.get("id") or "",
            "name": tool_call["name"],
            "arguments": json.dumps(tool_call.get("args", {})),
            "status": "completed",
        }
        for tool_call in msg.tool_calls
    ]


def _assistant_block_items(
    content: list[Any], base_id: str, seen_ids: set[str]
) -> list[dict[str, Any]]:
    """Items for a ``responses/v1`` AIMessage content block list.

    Reasoning and function_call blocks map one-to-one; consecutive text
    blocks merge into one assistant ``message``; any other typed block is
    already a Responses item and passes through.
    """
    items: list[dict[str, Any]] = []
    text_item: dict[str, Any] | None = None
    for block in content:
        if not isinstance(block, dict) or block.get("type") is None:
            continue
        block_type = block["type"]
        if block_type == "text":
            if text_item is None:
                text_item = {
                    "type": "message",
                    "id": _unique_item_id(block.get("id") or f"{base_id}:message", seen_ids),
                    "role": "assistant",
                    "content": [],
                    "status": "completed",
                }
                items.append(text_item)
            text_item["content"].append(_ai_text_block_to_part(block))
            continue
        text_item = None
        if block_type == "reasoning":
            items.append(
                {
                    "type": "reasoning",
                    "id": _unique_item_id(block.get("id") or f"{base_id}:reasoning", seen_ids),
                    "summary": block.get("summary", []),
                    "status": "completed",
                }
            )
        elif block_type == "function_call":
            items.append(
                {
                    "type": "function_call",
                    "id": _unique_item_id(
                        block.get("id") or block.get("call_id") or f"{base_id}:fc", seen_ids
                    ),
                    "call_id": block["call_id"],
                    "name": block["name"],
                    "arguments": str(block.get("arguments", "")),
                    "status": "completed",
                }
            )
        else:
            items.append(
                {
                    **block,
                    "id": _unique_item_id(block.get("id") or f"{base_id}:{block_type}", seen_ids),
                    "status": "completed",
                }
            )
    return items


def _image_url(block: dict[str, Any]) -> str | None:
    """URL (or data URL) of a LangChain image content block, any known shape."""
    if block.get("type") == "image_url":
        image_url = block.get("image_url")
        return image_url.get("url") if isinstance(image_url, dict) else image_url
    if block.get("url"):
        return str(block["url"])
    data = block.get("base64") or block.get("data")
    if data:
        return f"data:{block.get('mime_type') or 'image/png'};base64,{data}"
    return None


def _tool_output(content: Any) -> str | list[dict[str, Any]]:
    """``function_call_output.output``: a string, or Responses input content parts."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return json.dumps(content, default=str)
    parts: list[dict[str, Any]] = []
    for block in content:
        if isinstance(block, str):
            parts.append({"type": "input_text", "text": block})
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append({"type": "input_text", "text": str(block.get("text", ""))})
        elif isinstance(block, dict) and block.get("type") in ("image", "image_url"):
            url = _image_url(block)
            if url is not None:
                parts.append({"type": "input_image", "image_url": url, "detail": "auto"})
            else:
                parts.append({"type": "input_text", "text": json.dumps(block, default=str)})
        else:
            parts.append({"type": "input_text", "text": json.dumps(block, default=str)})
    return parts


def _message_items(
    msg: BaseMessage, index: int, conversation_id: str, seen_ids: set[str]
) -> list[dict[str, Any]]:
    """Conversation items for one LangChain message; never empty."""
    base_id = f"{conversation_id}:{index}"
    if isinstance(msg, (HumanMessage, SystemMessage)):
        return [
            {
                "type": "message",
                "id": _unique_item_id(msg.id or base_id, seen_ids),
                "role": "user" if isinstance(msg, HumanMessage) else "system",
                "content": _human_content_parts(msg.content),
                "status": "completed",
            }
        ]
    if isinstance(msg, ToolMessage):
        return [
            {
                "type": "function_call_output",
                "id": _unique_item_id(msg.id or f"{base_id}:tool", seen_ids),
                "call_id": msg.tool_call_id,
                "output": _tool_output(msg.content),
                "status": "completed",
            }
        ]

    items: list[dict[str, Any]] = []
    if isinstance(msg.content, list):
        items = _assistant_block_items(msg.content, base_id, seen_ids)
    elif isinstance(msg.content, str) and msg.content:
        items = [
            {
                "type": "message",
                "id": _unique_item_id(msg.id or f"{base_id}:message", seen_ids),
                "role": "assistant",
                "content": [{"type": "output_text", "text": msg.content, "annotations": []}],
                "status": "completed",
            }
        ]
    if (
        isinstance(msg, AIMessage)
        and msg.tool_calls
        and not any(item["type"] == "function_call" for item in items)
    ):
        items.extend(_function_call_items(msg, base_id, seen_ids))
    if not items:
        items.append(
            {
                "type": "message",
                "id": _unique_item_id(msg.id or f"{base_id}:message", seen_ids),
                "role": "assistant",
                "content": [],
                "status": "completed",
            }
        )
    if msg.response_metadata.get("status") == CANCELLED_STATUS:
        return [{**item, "status": "incomplete"} for item in items]
    return items


def conversation_items_with_sources(
    messages: list[BaseMessage], conversation_id: str
) -> list[tuple[int, dict[str, Any]]]:
    """Conversation items paired with the index of the message each came from."""
    seen_ids: set[str] = set()
    return [
        (index, item)
        for index, msg in enumerate(messages)
        for item in _message_items(msg, index, conversation_id, seen_ids)
    ]


def messages_to_conversation_items(
    messages: list[BaseMessage], conversation_id: str
) -> list[dict[str, Any]]:
    """Map LangChain messages to OpenAI Responses conversation items."""
    return [item for _, item in conversation_items_with_sources(messages, conversation_id)]


def with_langchain_messages(
    page: list[dict[str, Any]],
    source_index_by_item_id: dict[str, int],
    messages: list[BaseMessage],
    conversation_id: str,
) -> list[dict[str, Any]]:
    """Vendor include ``progenitor.langchain_message`` for one returned page.

    Every item gets ``source_message_id``; the first item of each source
    message within the page also gets the serialized LangChain message, so a
    client paging through the whole conversation can rebuild it losslessly.
    """
    annotated: list[dict[str, Any]] = []
    seen_sources: set[int] = set()
    for item in page:
        index = source_index_by_item_id[item["id"]]
        msg = messages[index]
        extra: dict[str, Any] = {"source_message_id": msg.id or f"{conversation_id}:{index}"}
        if index not in seen_sources:
            seen_sources.add(index)
            extra["langchain_message"] = messages_to_dict([msg])[0]
        annotated.append({**item, **extra})
    return annotated


def build_response_object(
    response_id: str,
    thread_id: str,
    model: str,
    status: str,
    output: list[dict[str, Any]],
    metadata: dict[str, Any] | None,
    error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a response object matching the OpenAI Responses API shape."""
    return {
        "id": response_id,
        "object": "response",
        "created_at": int(time.time()),
        "status": status,
        "model": model,
        "conversation": {"id": thread_id},
        "output": output,
        "error": error,
        "metadata": metadata or {},
    }


def paginate_items(
    items: list[dict[str, Any]],
    *,
    after: str | None,
    limit: int,
    order: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Paginate a list of conversation items.

    Returns (page_items, list_metadata).
    """
    if order == "desc":
        items = list(reversed(items))

    start_index = 0
    if after is not None:
        try:
            start_index = next(i for i, it in enumerate(items) if it["id"] == after) + 1
        except StopIteration:
            raise ValueError(f"after cursor '{after}' not found") from None

    page = items[start_index : start_index + limit]
    has_more = (start_index + limit) < len(items)

    first_id = page[0]["id"] if page else None
    last_id = page[-1]["id"] if page else None

    return page, {
        "object": "list",
        "first_id": first_id,
        "last_id": last_id,
        "has_more": has_more,
    }


def build_sse_event(event_type: str, data: dict[str, Any], sequence_number: int) -> dict[str, Any]:
    """Wrap a data dict with the required SSE fields."""
    return {"type": event_type, "sequence_number": sequence_number, **data}
