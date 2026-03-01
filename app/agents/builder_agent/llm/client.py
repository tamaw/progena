"""Azure Responses API client for Builder Agent."""

import json
import logging
import re
from collections.abc import AsyncIterator
from typing import Any, TypeVar, cast
from urllib.parse import urlsplit, urlunsplit

import httpx
from pydantic import BaseModel

from ..config import LLMConfig, MCPConfig
from ..tools.user_communication import EventPusher
from .prompts import PromptTemplates
from .response_models import (
    BuildSpec,
    DecisionResponse,
    PlannerResponse,
    validate_build_spec,
    validate_decision_response,
    validate_planner_response,
)
from .tokenizer import count_message_tokens

logger = logging.getLogger(__name__)

TModel = TypeVar("TModel", bound=BaseModel)

# Context window sizes for common models (in tokens)
MODEL_CONTEXT_WINDOWS = {
    # GPT-4 models
    "gpt-4": 8192,
    "gpt-4-32k": 32768,
    "gpt-4-turbo": 128000,
    "gpt-4-turbo-preview": 128000,
    "gpt-4o": 128000,
    "gpt-4o-mini": 128000,
    # GPT-3.5 models
    "gpt-3.5-turbo": 16385,
    "gpt-3.5-turbo-16k": 16385,
    # Claude models
    "claude-3-opus": 200000,
    "claude-3-sonnet": 200000,
    "claude-3-haiku": 200000,
    "claude-3-5-sonnet": 200000,
    "claude-3-5-haiku": 200000,
    # Fallback
    "default": 8192,
}

INLINE_TOOL_MARKUP_PATTERN = re.compile(r".*?", re.DOTALL)


def _build_responses_endpoint(base_url: str) -> str:
    """Build a valid /responses endpoint from either base URL or full endpoint URL."""
    raw = (base_url or "").strip()
    if not raw:
        return "/responses"

    parsed = urlsplit(raw)
    path = (parsed.path or "").rstrip("/")
    endpoint_path = path if path.endswith("/responses") else f"{path}/responses"

    return urlunsplit((parsed.scheme, parsed.netloc, endpoint_path, parsed.query, parsed.fragment))


def _build_json_schema_format(response_model: Any, schema_name: str) -> dict[str, Any]:
    """Build response_format dict for structured output using Pydantic model.

    Args:
        response_model: Pydantic BaseModel class to generate schema from
        schema_name: Name for the JSON schema

    Returns:
        Dictionary in format: {"type": "json_schema", "json_schema": {...}}
    """
    schema = response_model.model_json_schema()
    schema["additionalProperties"] = False
    if "properties" in schema:
        schema["required"] = list(schema["properties"].keys())

    return {
        "type": "json_schema",
        "json_schema": {
            "name": schema_name,
            "schema": schema,
            "strict": True,
        }
    }


class LLMClient:
    """Simple wrapper for Azure OpenAI /responses endpoint."""

    def __init__(
        self,
        config: LLMConfig | None = None,
        mcp_config: MCPConfig | None = None,
        role: str | None = None,
    ):
        """Initialize the LLM client.

        Args:
            config: LLM configuration. If None, loads from environment.
            mcp_config: MCP configuration. If None, loads from environment.
            role: Optional role for per-role model selection (planner, builder, summary).
        """
        self.config = config or LLMConfig.from_env()
        self.mcp_config = mcp_config or MCPConfig.from_env()
        self.prompts = PromptTemplates()
        self.role = role
        self.model = self.config.get_model_for_role(role) if role else self.config.model_default

        self.endpoint_url = _build_responses_endpoint(self.config.base_url)

        api_key = self.config.api_key or ""
        headers = {
            "Content-Type": "application/json",
            "api-key": api_key,
        }

        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(120.0),
            headers=headers,
        )

        # MCP context is initialized externally (startup)
        self.mcp_client: httpx.AsyncClient | None = None
        self.mcp_tools: list[dict[str, Any]] = []

        # Optional event pusher for realtime progress updates via IPC
        self._event_pusher: EventPusher | None = None

        logger.info("LLM client configured (endpoint=%s, model=%s)", self.endpoint_url, self.model)

    def set_event_pusher(self, event_pusher: EventPusher | None) -> None:
        """Attach an event pusher used to stream progress updates to clients."""
        self._event_pusher = event_pusher

    async def _push_event(
        self,
        event_type: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if not self._event_pusher:
            return
        try:
            await self._event_pusher(event_type, content, metadata)
        except Exception:
            # Never let event delivery break the main LLM/tool flow
            logger.debug("Failed to push event %s", event_type, exc_info=True)

    async def close(self):
        """Close the HTTP client."""
        await self.client.aclose()
        if self.mcp_client:
            await self.mcp_client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    def _build_input_messages(
        self,
        messages: list[dict[str, Any]],
        system_prompt: str | None = None,
    ) -> list[dict[str, Any]]:
        """Build Responses API input messages from chat history."""
        input_messages: list[dict[str, Any]] = []

        allowed_roles = {"system", "user", "assistant", "tool"}

        if system_prompt:
            input_messages.append({
                "role": "system",
                "content": system_prompt,
            })

        for msg in messages:
            role = str(msg.get("role", "user") or "user")
            if role not in allowed_roles:
                # Scheduled/background messages may use custom roles like
                # "scheduler". The OpenAI-compatible Responses API only
                # accepts standard roles, so coerce unknown roles to "user".
                role = "user"
            content = msg.get("content", "")

            if isinstance(content, list):
                if content and isinstance(content[0], dict) and content[0].get("type") in {
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
                }:
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

            input_messages.append({
                "role": role,
                "content": wrapped_content,
            })

        return input_messages

    def set_mcp_context(
        self,
        mcp_client: httpx.AsyncClient | None,
        mcp_tools: list[dict[str, Any]] | None = None,
    ) -> None:
        """Set MCP client and tool list for tool calling."""
        self.mcp_client = mcp_client
        self.mcp_tools = mcp_tools or []

    def get_mcp_tool_registry_entries(self) -> list[dict[str, Any]]:
        """Return MCP tools formatted for tool registry storage."""
        entries: list[dict[str, Any]] = []
        for tool in self.mcp_tools:
            name = tool.get("name")
            if not name:
                continue
            entry = {
                "name": name,
                "description": tool.get("description", "MCP tool"),
                "type": "mcp_tool",
                "source": "mcp",
                "endpoint": self.mcp_config.base_url,
                "transport": "http",
                "capabilities": tool.get("capabilities") or [name],
            }
            entries.append(entry)
        return entries

    def _normalize_tool_definition(self, tool: dict[str, Any]) -> dict[str, Any] | None:
        """Normalize a tool definition into OpenAI-compatible format.

        Handles both OpenAI format and MCP format tools.
        """
        # Already in OpenAI format
        if tool.get("type") == "function" and "function" in tool:
            return tool

        # Extract from MCP format
        name = tool.get("name")
        if not name:
            return None

        description = tool.get("description") or tool.get("summary") or "MCP tool"
        parameters = (
            tool.get("input_schema")
            or tool.get("inputSchema")
            or tool.get("parameters")
            or {"type": "object", "properties": {}}
        )

        return {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": parameters,
            },
        }

    def _merge_tools(self, tools: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
        """Merge caller-supplied tools with MCP tools, de-duplicated by name."""
        if tools is not None and len(tools) == 0:
            return []

        merged: list[dict[str, Any]] = []
        seen_names: set[str] = set()

        for tool in tools or []:
            name = tool.get("function", {}).get("name") if tool.get("type") == "function" else tool.get("name")
            if name:
                seen_names.add(name)
            merged.append(tool)

        for tool in self.mcp_tools:
            normalized = self._normalize_tool_definition(tool)
            if not normalized:
                continue
            name = normalized.get("function", {}).get("name")
            if not name or name in seen_names:
                continue
            seen_names.add(name)
            merged.append(normalized)

        return merged

    def _resolve_request_tools(
        self,
        tools: list[dict[str, Any]] | None,
        response_format: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        """Resolve effective tools for a request.

        For structured-output requests, we still include the tool list in the
        upstream payload (so the provider can see
        the full tool registry), but the call site should typically force
        tool_choice='none' to avoid tool calls breaking strict JSON outputs.
        """
        if response_format is not None and tools is None:
            # Default tools for structured outputs: include for context/contract,
            # but rely on tool_choice='none' during the call.
            return self._merge_tools(None)
        return self._merge_tools(tools)

    def _extract_tool_calls(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        """Extract tool calls from a Responses API payload.

        Tool calls can appear in:
        - data["output"] as {"type": "tool_call", ...}
        - data["output"] as {"type": "function_call", ...}
        - data["output"] > message > content as tool_call items
        - data["tool_calls"] as direct list
        """
        tool_calls: list[dict[str, Any]] = []
        tool_call_types = {"tool_call", "function_call"}

        # Check output array
        for item in data.get("output", []):
            if item.get("type") in tool_call_types:
                tool_calls.append(item)
                continue

            # Check message content for tool calls
            if item.get("type") == "message":
                content = item.get("content")
                if isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict) and part.get("type") in tool_call_types:
                            tool_calls.append(part)

        # Check top-level tool_calls
        if isinstance(data.get("tool_calls"), list):
            tool_calls.extend(data["tool_calls"])

        return tool_calls

    async def _call_mcp_tool(self, tool_call: dict[str, Any]) -> dict[str, Any]:
        """Execute a tool call against the MCP server."""
        # Extract tool name
        name = (
            tool_call.get("name")
            or tool_call.get("tool_name")
            or tool_call.get("function", {}).get("name")
        )
        if not name:
            return {"error": "Tool call missing name"}

        if not self.mcp_config.enabled:
            return {"error": "MCP integration disabled"}

        # Extract arguments
        arguments = (
            tool_call.get("arguments")
            or tool_call.get("input")
            or tool_call.get("params")
            or tool_call.get("function", {}).get("arguments")
            or {}
        )

        # Parse JSON string arguments
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                arguments = {"raw": arguments}

        if not self.mcp_client:
            return {"error": "MCP client not initialized"}

        logger.info("Calling MCP tool: %s", name)

        request = {
            "jsonrpc": "2.0",
            "id": tool_call.get("call_id") or tool_call.get("id") or tool_call.get("tool_call_id") or 1,
            "method": "tools/call",
            "params": {
                "name": name,
                "arguments": arguments,
            },
        }

        try:
            response = await self.mcp_client.post(self.mcp_config.base_url, json=request)
            response.raise_for_status()
            payload = response.json()

            if isinstance(payload, dict):
                if error := payload.get("error"):
                    return {"error": error}
                return cast(dict[str, Any], payload.get("result", payload))
            return cast(dict[str, Any], payload)

        except Exception as exc:
            logger.warning("MCP tool call failed: %s", exc)
            return {"error": f"MCP call failed: {exc}"}

    async def _execute_tool_call(
        self,
        tool_call: dict[str, Any],
        tool_name: str,
        tool_args: dict[str, Any],
        system_tool_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute either a system tool or an MCP tool call."""
        if system_tool_context:
            get_system_tool: Any = None
            execute_system_tool: Any = None
            try:
                from ..nodes.node_helpers.registry import execute_system_tool, get_system_tool
            except Exception:
                get_system_tool = None
                execute_system_tool = None

            if callable(get_system_tool) and callable(execute_system_tool) and get_system_tool(tool_name):
                tool_registry = system_tool_context.get("tool_registry")
                state_manager = system_tool_context.get("state_manager")
                state = system_tool_context.get("state")
                default_user_input = system_tool_context.get("default_user_input", "")
                tool_user_input = str(tool_args.get("request") or default_user_input or "")

                data_out, err, state_updates, _retry = await execute_system_tool(  # pyright: ignore[reportGeneralTypeIssues]
                    tool_name=tool_name,
                    tool_registry=tool_registry,
                    llm_client=self,
                    state=state,
                    state_manager=state_manager,
                    user_input=tool_user_input,
                    tool_args=tool_args,
                )

                if state_updates and isinstance(system_tool_context.get("state_updates"), dict):
                    system_tool_context["state_updates"].update(state_updates)
                if state_updates and isinstance(state, dict):
                    state.update(state_updates)

                if err:
                    return {"error": err}
                return {"result": data_out}

            # If it isn't a system tool, allow direct A2A agent invocation by name.
            tool_registry = system_tool_context.get("tool_registry")
            if tool_registry is not None and hasattr(tool_registry, "find_by_name"):
                try:
                    agent_cfg = tool_registry.find_by_name(tool_name)
                except Exception:
                    agent_cfg = None

                if isinstance(agent_cfg, dict) and agent_cfg.get("kind") == "a2a_agent":
                    from ..tools.mcp import A2AClient

                    req = str(tool_args.get("request") or "")
                    ctx = tool_args.get("context") if isinstance(tool_args.get("context"), dict) else {}
                    result = await A2AClient().call_agent(
                        agent_config=agent_cfg,
                        request=req,
                        context=ctx,
                        timeout=60.0,
                    )
                    if isinstance(result, dict) and result.get("error"):
                        return {"error": str(result.get("error"))}
                    return {"result": result}

        # Default: call MCP tool
        return await self._call_mcp_tool({**tool_call, "arguments": tool_args})

    def _get_json_response_format(self, response_model: type, schema_name: str) -> dict[str, Any]:
        """Return a response_format payload for structured output."""
        return _build_json_schema_format(response_model, schema_name)

    @staticmethod
    def _to_azure_text_format(response_format: dict[str, Any]) -> dict[str, Any]:
        if response_format.get("type") != "json_schema":
            return response_format

        json_schema = response_format.get("json_schema")
        if not isinstance(json_schema, dict):
            return response_format

        return {
            "type": "json_schema",
            "name": json_schema.get("name") or "structured_output",
            "schema": json_schema.get("schema") or {},
            "strict": bool(json_schema.get("strict", True)),
        }

    def _extract_text_and_reasoning(self, data: dict[str, Any]) -> tuple[str, str]:
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
                for key in ("text", "value", "content", "output_text", "refusal", "message", "parts"):
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

        text = self._sanitize_user_visible_text("".join(text_parts).strip())
        reasoning = "".join(reasoning_parts).strip()

        if not text:
            error_payload = data.get("error")
            status = data.get("status")
            incomplete_details = data.get("incomplete_details")
            truncation = data.get("truncation")
            top_text = data.get("text")
            output_items: list[Any] = data.get("output") if isinstance(data.get("output"), list) else []  # type: ignore[assignment]

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

    @staticmethod
    def _sanitize_user_visible_text(text: str) -> str:
        """Remove provider/tool markup artifacts from user-visible text."""
        if not text:
            return text

        # Some providers emit inline tool-call traces (e.g.
        # "tool_name{...json...}") as plain text. Strip these blocks so
        # user-facing responses remain clean.
        cleaned = INLINE_TOOL_MARKUP_PATTERN.sub("", text)

        # Normalize accidental leading blank lines introduced by stripping.
        return cleaned.lstrip()

    def _supports_temperature(self) -> bool:
        """Return True if temperature parameter is supported for the current model."""
        # Some models don't support temperature parameter
        unsupported_keywords = {"gpt-5.2-chat"}
        model_lower = self.model.lower()
        return not any(keyword in model_lower for keyword in unsupported_keywords)

    def _is_temperature_unsupported(self, response: httpx.Response | None) -> bool:
        """Detect unsupported temperature errors from provider responses."""
        if response is None:
            return False
        try:
            data = response.json()
            message = str(data.get("error", {}).get("message", ""))
        except Exception:
            message = response.text
        message_lower = message.lower()
        return "temperature" in message_lower and "unsupported" in message_lower

    def _get_context_window_size(self, model: str | None = None) -> int:
        """Get context window size for a model."""
        target_model = model or self.model
        target_model_lower = target_model.lower()

        # Check exact match first
        for model_key, size in MODEL_CONTEXT_WINDOWS.items():
            if model_key in target_model_lower:
                return size

        return MODEL_CONTEXT_WINDOWS["default"]

    def _check_token_usage(self, messages: list[dict[str, Any]], model: str | None = None) -> None:
        """Check token usage and log warning if exceeding 50% of context window."""
        try:
            token_count = count_message_tokens(messages, model or self.model)
            context_window = self._get_context_window_size(model)
            usage_percent = (token_count / context_window) * 100

            if usage_percent > 50:
                logger.warning(
                    "Token usage at %.1f%% of context window: %d/%d tokens (model=%s)",
                    usage_percent,
                    token_count,
                    context_window,
                    model or self.model
                )
        except Exception as e:
            logger.debug("Failed to check token usage: %s", e)

    async def _post_response(
        self,
        input_messages: list[dict[str, Any]],
        response_format: dict[str, Any] | None = None,
        include_reasoning: bool = False,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        max_tool_rounds: int = 4,
        system_tool_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Call /responses endpoint with optional tool calling loop."""
        # Check token usage before making the call
        self._check_token_usage(input_messages)

        merged_tools = self._resolve_request_tools(tools, response_format)

        payload: dict[str, Any] = {
            "model": self.model,
            "input": input_messages,
            "max_output_tokens": max_tokens,
        }

        if response_format:
            payload["text"] = {"format": self._to_azure_text_format(response_format)}
        if merged_tools:
            payload["tools"] = merged_tools
            # When requesting strict structured output, prevent tool calling while
            # still sending tool definitions upstream.
            if tool_choice is not None:
                payload["tool_choice"] = tool_choice
            elif response_format is not None:
                payload["tool_choice"] = "none"
            else:
                # Be explicit: some providers default tool calling off unless
                # tool_choice is set.
                payload["tool_choice"] = "auto"
        if include_reasoning:
            payload["reasoning"] = {"summary": "auto"}
        if self._supports_temperature():
            payload["temperature"] = temperature

        # Initial call
        try:
            response = await self.client.post(self.endpoint_url, json=payload)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as exc:
            # Retry without temperature if unsupported
            if self._is_temperature_unsupported(exc.response):
                logger.debug("Temperature unsupported, retrying without it")
                payload.pop("temperature", None)
                response = await self.client.post(self.endpoint_url, json=payload)
                response.raise_for_status()
                data = response.json()
            else:
                raise

        # Tool calling loop (MCP + system tools)
        tool_round = 0
        tool_calls = self._extract_tool_calls(data)
        seen_calls: set[str] = set()

        while tool_calls and tool_round < max_tool_rounds:
            logger.info("Executing %d tool call(s) - round %d", len(tool_calls), tool_round + 1)
            await self._push_event(
                event_type="mcp_tool_round_started",
                content=f"Executing {len(tool_calls)} tool call(s) (round {tool_round + 1})",
                metadata={"round": tool_round + 1, "count": len(tool_calls)},
            )

            tool_result_messages: list[dict[str, Any]] = []

            for tool_call in tool_calls:
                tool_call_id = tool_call.get("call_id") or tool_call.get("id") or tool_call.get("tool_call_id")
                tool_name = (
                    tool_call.get("name")
                    or tool_call.get("tool_name")
                    or tool_call.get("function", {}).get("name")
                    or "(unknown)"
                )

                raw_args = (
                    tool_call.get("arguments")
                    or tool_call.get("input")
                    or tool_call.get("params")
                    or tool_call.get("function", {}).get("arguments")
                    or {}
                )
                if isinstance(raw_args, str):
                    try:
                        tool_args = json.loads(raw_args)
                    except json.JSONDecodeError:
                        tool_args = {"raw": raw_args}
                elif isinstance(raw_args, dict):
                    tool_args = raw_args
                else:
                    tool_args = {"raw": raw_args}

                # Safety: detect repeated identical tool calls.
                try:
                    sig = f"{tool_name}:{json.dumps(tool_args, sort_keys=True, default=str)}"
                except Exception:
                    sig = f"{tool_name}:{str(tool_args)}"

                await self._push_event(
                    event_type="mcp_tool_call_started",
                    content=f"Calling tool: {tool_name}",
                    metadata={
                        "tool": tool_name,
                        "tool_call_id": tool_call_id,
                        "type": tool_call.get("type"),
                    },
                )

                if sig in seen_calls:
                    result: dict[str, Any] = {"error": f"Repeated tool call detected: {tool_name}"}
                else:
                    seen_calls.add(sig)
                    result = await self._execute_tool_call(
                        tool_call=tool_call,
                        tool_name=tool_name,
                        tool_args=tool_args,
                        system_tool_context=system_tool_context,
                    )

                success = not (isinstance(result, dict) and result.get("error"))
                await self._push_event(
                    event_type="mcp_tool_call_finished",
                    content=f"Tool {tool_name} {'succeeded' if success else 'failed'}",
                    metadata={
                        "tool": tool_name,
                        "tool_call_id": tool_call_id,
                        "success": success,
                        "error": (result.get("error") if isinstance(result, dict) else None),
                    },
                )

                result_text = json.dumps(result, default=str)
                tool_result_messages.append(
                    {
                        "role": "user",
                        "content": f"[TOOL_RESULT tool={tool_name} id={tool_call_id}] {result_text}",
                    }
                )

            # Feed tool results back into the next round as plain-text input.
            input_messages.extend(tool_result_messages)
            payload["input"] = input_messages

            # Next round
            response = await self.client.post(self.endpoint_url, json=payload)
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                body_preview = (exc.response.text or "").strip()
                if len(body_preview) > 2000:
                    body_preview = body_preview[:2000] + "..."
                logger.error(
                    "Tool-loop follow-up /responses request failed (status=%s): %s",
                    exc.response.status_code,
                    body_preview,
                )
                await self._push_event(
                    event_type="mcp_tool_followup_failed",
                    content=f"Tool-loop follow-up request failed ({exc.response.status_code})",
                    metadata={
                        "status_code": exc.response.status_code,
                        "body": body_preview,
                    },
                )
                raise

            data = response.json()
            tool_calls = self._extract_tool_calls(data)
            tool_round += 1

            await self._push_event(
                event_type="mcp_tool_round_finished",
                content=f"Finished tool round {tool_round}",
                metadata={"round": tool_round, "remaining_calls": len(tool_calls)},
            )

        if tool_calls:
            logger.warning("Tool call loop reached max rounds (%d)", max_tool_rounds)
            await self._push_event(
                event_type="mcp_tool_round_limit_reached",
                content=f"Tool call loop reached max rounds ({max_tool_rounds})",
                metadata={"max_tool_rounds": max_tool_rounds, "remaining_calls": len(tool_calls)},
            )

        return cast(dict[str, Any], data)

    async def chat(
        self,
        messages: list[dict[str, str]],
        system_prompt: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        response_format: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
        max_tool_rounds: int = 4,
        system_tool_context: dict[str, Any] | None = None,
    ) -> str:
        """Call /responses endpoint and return text response."""
        if response_format:
            instruction = "Return only valid JSON that matches the provided schema. Do not include any extra text."
            system_prompt = f"{system_prompt}\n\n{instruction}" if system_prompt else instruction
        input_messages = self._build_input_messages(messages, system_prompt)

        data = await self._post_response(
            input_messages=input_messages,
            response_format=response_format,
            include_reasoning=False,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tools,
            max_tool_rounds=max_tool_rounds,
            system_tool_context=system_tool_context,
        )
        text, _reasoning = self._extract_text_and_reasoning(data)
        return text

    async def chat_structured(
        self,
        messages: list[dict[str, str]],
        response_model: type[TModel],
        schema_name: str,
        system_prompt: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tools: list[dict[str, Any]] | None = None,
    ) -> TModel:
        """Call /responses endpoint and return a validated structured model."""
        response_format = self._get_json_response_format(response_model, schema_name)
        response = await self.chat(
            messages=messages,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
            tools=tools,
        )
        raw_data = json.loads(response)
        return response_model.model_validate(raw_data)

    async def chat_with_reasoning(
        self,
        messages: list[dict[str, str]],
        system_prompt: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        response_format: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> tuple[str, str]:
        """Call /responses endpoint and return text plus reasoning trace."""
        input_messages = self._build_input_messages(messages, system_prompt)

        data = await self._post_response(
            input_messages=input_messages,
            response_format=response_format,
            include_reasoning=True,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tools,
        )
        return self._extract_text_and_reasoning(data)

    async def chat_stream(
        self,
        messages: list[dict[str, str]],
        system_prompt: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[str]:
        """Stream not supported, returns full response as single chunk."""
        response = await self.chat(messages, system_prompt, temperature, max_tokens, tools=tools)
        yield response

    async def decide(
        self,
        user_input: str,
        conversation_history: list[dict[str, str]],
        tool_context: str,
    ) -> dict[str, Any]:
        """Make a routing decision for user input."""
        system_prompt = self.prompts.get_decide_prompt(tool_context)
        messages = conversation_history[-10:] + [{"role": "user", "content": user_input}]
        schema_hint = json.dumps(DecisionResponse.model_json_schema(), separators=(",", ":"), ensure_ascii=True)
        instruction = (
            "Return ONLY valid JSON (no markdown, no extra text) that matches this JSON Schema: "
            f"{schema_hint}"
        )
        response = await self.chat(messages, f"{system_prompt}\n\n{instruction}", temperature=0.3)
        if not response.strip():
            logger.warning("Empty decide response; returning default decision")
            validated = validate_decision_response({})
            return validated.model_dump()
        try:
            raw_data = json.loads(response)
            validated = validate_decision_response(raw_data)
            return validated.model_dump()
        except json.JSONDecodeError:
            preview = response.strip().replace("\n", " ")[:500]
            logger.warning("Non-JSON decide response; preview=%s", preview)
            start = response.find("{")
            end = response.rfind("}")
            if start != -1 and end != -1 and end > start:
                try:
                    raw_data = json.loads(response[start:end + 1])
                    validated = validate_decision_response(raw_data)
                    return validated.model_dump()
                except json.JSONDecodeError:
                    pass
            validated = validate_decision_response({})
            return validated.model_dump()

    async def plan(
        self,
        user_input: str,
        decision: dict[str, Any],
        current_backlog: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Generate work items for a task."""
        system_prompt = self.prompts.get_planner_prompt()
        context = self.prompts.get(
            "builder_agent/planner_user_message.md",
            user_input=user_input,
            decision=str(decision),
            current_backlog=str(current_backlog),
        )
        messages = [{"role": "user", "content": context}]
        response_format = _build_json_schema_format(PlannerResponse, "planner_response")

        response = await self.chat(messages, system_prompt, temperature=0.5, response_format=response_format)
        raw_data = json.loads(response)
        validated = validate_planner_response(raw_data)
        return [item.model_dump() for item in validated.work_items]

    async def generate_response(
        self,
        user_input: str,
        conversation_history: list[dict[str, str]],
        action_results: list[dict[str, Any]] | None = None,
    ) -> str:
        """Generate a response to the user."""
        system_prompt = self.prompts.get_genesis_prompt()
        messages = conversation_history[-20:]

        if action_results:
            results_summary = "\n".join([
                f"- {r.get('action', 'unknown')}: {r.get('result', 'completed')}"
                for r in action_results
            ])
            messages.append({
                "role": "system",
                "content": f"Actions completed:\n{results_summary}\n\nRespond to the user about these results."
            })

        messages.append({"role": "user", "content": user_input})
        return await self.chat(messages, system_prompt, temperature=0.7)

    async def generate(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> str:
        """Generate text response for a prompt."""
        messages = [{"role": "user", "content": prompt}]
        return await self.chat(messages, system_prompt, temperature, max_tokens)

    async def generate_with_reasoning(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> tuple[str, str]:
        """Generate text response and return reasoning trace when available."""
        messages = [{"role": "user", "content": prompt}]
        return await self.chat_with_reasoning(messages, system_prompt, temperature, max_tokens)

    async def generate_build_spec(
        self,
        item_type: str,
        description: str,
        capabilities: list[str],
    ) -> dict[str, Any]:
        """Generate a specification for building an agent or MCP server."""
        system_prompt = self.prompts.get_build_prompt(item_type)
        context = self.prompts.get(
            "builder_agent/build_spec_user_message.md",
            item_type=item_type,
            description=description,
            capabilities=", ".join(capabilities),
        )
        messages = [{"role": "user", "content": context}]
        response_format = _build_json_schema_format(BuildSpec, "build_spec")

        response = await self.chat(messages, system_prompt, temperature=0.5, response_format=response_format)
        raw_data = json.loads(response)
        validated = validate_build_spec(raw_data)
        return validated.model_dump()

    async def complete(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> str:
        """Complete a prompt. Alias for generate()."""
        return await self.generate(prompt, system_prompt, temperature, max_tokens)
