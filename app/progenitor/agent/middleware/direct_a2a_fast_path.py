from __future__ import annotations

import contextlib
import inspect
import re
from collections.abc import Awaitable, Callable
from typing import Any, cast

from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import AIMessage

from app.progenitor.a2a import A2AClient, extract_message_text, summarize_task_payload

_DIRECT_A2A_RE = re.compile(r"^@(?P<agent>[A-Za-z0-9_][A-Za-z0-9_.-]*)\s+(?P<request>.+?)\s*$")
_DIRECT_A2A_EMPTY_RE = re.compile(r"^@(?P<agent>[A-Za-z0-9_][A-Za-z0-9_.-]*)\s*$")
_DISPLAY_KEYS = ("display_agent_name", "display_model_name", "display_tool_calls")


class DirectA2AFastPathMiddleware(AgentMiddleware):
    """Short-circuit model calls for direct `@agent_name request` messages."""

    def __init__(
        self,
        *,
        tool_registry: Any = None,
        timeout_seconds: float = 60.0,
        client_factory: Callable[[], A2AClient] | None = None,
    ) -> None:
        self._tool_registry = tool_registry
        self._timeout_seconds = timeout_seconds
        self._client_factory = client_factory or A2AClient

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[Any]],
    ) -> Any:
        user_text = _latest_user_message_text(request)
        if user_text is None:
            return await handler(request)

        empty_match = _DIRECT_A2A_EMPTY_RE.match(user_text)
        match = _DIRECT_A2A_RE.match(user_text)
        if empty_match and not match:
            agent_name = empty_match.group("agent")
            return _direct_reply(
                f"Direct agent call failed: '@{agent_name}' requires a request after the agent name."
            )
        if not match:
            return await handler(request)

        agent_name = match.group("agent")
        request_text = match.group("request").strip()
        if not request_text:
            return _direct_reply(
                f"Direct agent call failed: '@{agent_name}' requires a request after the agent name."
            )

        await _best_effort_reload_registry(self._tool_registry)
        cfg = _find_exact_agent(self._tool_registry, agent_name)
        if not isinstance(cfg, dict):
            return _direct_reply(f"Direct agent call failed: unknown agent '{agent_name}'.")

        if str(cfg.get("transport") or "").lower() != "http":
            return _direct_reply(
                f"Direct agent call failed: '{agent_name}' is not an HTTP A2A agent."
            )
        if not str(cfg.get("url") or "").strip():
            return _direct_reply(
                f"Direct agent call failed: '{agent_name}' does not have a configured URL."
            )

        thread_id = _extract_thread_id(request.state)
        response = await self._client_factory().call_agent(
            cfg,
            request_text,
            blocking=True,
            timeout=self._timeout_seconds,
            context={
                "caller": "progenitor",
                "routing_mode": "direct_prefix",
                "thread_id": thread_id,
            },
        )
        if not response.get("success"):
            return _direct_reply(
                f"Direct agent call failed: {response.get('error') or 'unknown error'}."
            )

        payload = response.get("result")
        content = _extract_direct_reply_text(payload, agent_name)
        response_metadata = _extract_display_metadata(payload)
        return ModelResponse(
            result=[AIMessage(content=content, response_metadata=response_metadata)]
        )


def _latest_user_message_text(request: ModelRequest[Any]) -> str | None:
    for message in reversed(request.messages or []):
        role = str(getattr(message, "type", "") or getattr(message, "role", "")).lower()
        if role not in {"human", "user"}:
            continue
        content = getattr(message, "content", None)
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                text = getattr(item, "text", None)
                if isinstance(text, str):
                    parts.append(text)
                elif isinstance(item, dict) and isinstance(item.get("text"), str):
                    parts.append(item["text"])
            return "".join(parts)
    return None


async def _best_effort_reload_registry(tool_registry: Any) -> None:
    load = getattr(tool_registry, "load", None)
    if not callable(load):
        return
    with contextlib.suppress(Exception):
        maybe = load()
        if inspect.isawaitable(maybe):
            await cast(Awaitable[Any], maybe)


def _find_exact_agent(tool_registry: Any, agent_name: str) -> dict[str, Any] | None:
    find_by_name = getattr(tool_registry, "find_by_name", None)
    if not callable(find_by_name):
        return None
    with contextlib.suppress(Exception):
        found = find_by_name(agent_name)
        return dict(found) if isinstance(found, dict) else None
    return None


def _extract_thread_id(state: Any) -> str | None:
    if not isinstance(state, dict):
        return None
    thread_id = str(state.get("thread_id") or "").strip()
    if thread_id:
        return thread_id
    configurable = state.get("configurable")
    if isinstance(configurable, dict):
        value = str(configurable.get("thread_id") or "").strip()
        if value:
            return value
    return None


def _extract_direct_reply_text(payload: Any, agent_name: str) -> str:
    if isinstance(payload, dict):
        status = payload.get("status")
        if isinstance(status, dict):
            text = extract_message_text(status.get("message"))
            if text:
                return text
        text = summarize_task_payload(payload)
        if text:
            return text
    return f"{agent_name} completed the request."


def _extract_display_metadata(payload: Any) -> dict[str, Any]:
    candidates: list[Any] = []
    if isinstance(payload, dict):
        candidates.append(payload.get("metadata"))
        status = payload.get("status")
        if isinstance(status, dict):
            message = status.get("message")
            if isinstance(message, dict):
                candidates.append(message.get("metadata"))
        history = payload.get("history")
        if isinstance(history, list):
            candidates.extend(
                item.get("metadata")
                for item in history
                if isinstance(item, dict) and item.get("metadata")
            )
        artifacts = payload.get("artifacts")
        if isinstance(artifacts, list):
            candidates.extend(
                item.get("metadata")
                for item in artifacts
                if isinstance(item, dict) and item.get("metadata")
            )

    normalized: dict[str, Any] = {}
    for metadata in candidates:
        if not isinstance(metadata, dict):
            continue
        for key in _DISPLAY_KEYS:
            value = metadata.get(key)
            if value in (None, "", []):
                continue
            if key == "display_tool_calls":
                if isinstance(value, list):
                    normalized[key] = value
                continue
            if isinstance(value, str):
                normalized[key] = value
    return normalized


def _direct_reply(message: str) -> ModelResponse:
    return ModelResponse(result=[AIMessage(content=message)])
