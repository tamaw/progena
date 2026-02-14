"""
LiteLLM API Client for Progenitor.

Uses the LiteLLM /responses endpoint for all model calls.
LiteLLM handles retries, provider routing, and observability upstream.
"""

import json
import logging
import os
from collections.abc import AsyncIterator
from typing import Any, TypeVar
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel

from ..config import LLMConfig, MCPConfig
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


def _build_json_schema_format(response_model: type, schema_name: str) -> dict[str, Any]:
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
    """Simple wrapper for LiteLLM /responses endpoint."""

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

        base_url = self.config.base_url.rstrip('/')
        self.endpoint_url = f"{base_url}/responses"

        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(120.0),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.config.api_key or 'unused'}",
            }
        )

        self.structured_override_url = self._resolve_structured_override_url()
        self.structured_override_api_key = (
            os.getenv("PROGENITOR_AZURE_STRUCTURED_API_KEY")
            or os.getenv("AZURE_OPENAI_API_KEY")
        )
        self.structured_override_model = (
            os.getenv("PROGENITOR_AZURE_STRUCTURED_MODEL")
            or os.getenv("AZURE_OPENAI_DEPLOYMENT_DEFAULT")
        )

        # MCP context is initialized externally (startup)
        self.mcp_client: httpx.AsyncClient | None = None
        self.mcp_tools: list[dict[str, Any]] = []

        logger.info("LLM client configured (endpoint=%s, model=%s)", self.endpoint_url, self.model)

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

        if system_prompt:
            input_messages.append({
                "role": "system",
                "content": system_prompt,
            })

        for msg in messages:
            role = msg.get("role", "user")
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
                    wrapped_content = content
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
                "source": "litellm",
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

        For structured-output requests, tools are disabled by default unless
        explicitly provided by the caller.
        """
        if response_format is not None and tools is None:
            return []
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
                return payload.get("result", payload)
            return payload

        except Exception as exc:
            logger.warning("MCP tool call failed: %s", exc)
            return {"error": f"MCP call failed: {exc}"}

    def _get_json_response_format(self, response_model: type, schema_name: str) -> dict[str, Any]:
        """Return a response_format payload for structured output."""
        return _build_json_schema_format(response_model, schema_name)

    @staticmethod
    def _normalize_azure_base(endpoint: str) -> str:
        endpoint = endpoint.strip()
        parsed = urlparse(endpoint)
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}"
        return endpoint.rstrip("/")

    def _resolve_structured_override_url(self) -> str | None:
        explicit_url = os.getenv("PROGENITOR_AZURE_STRUCTURED_RESPONSES_URL")
        if explicit_url:
            return explicit_url

        endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
        if not endpoint:
            return None

        api_version = os.getenv("AZURE_OPENAI_API_VERSION", "preview")
        base = self._normalize_azure_base(endpoint)
        return f"{base}/openai/v1/responses?api-version={api_version}"

    def _can_use_structured_override(self) -> bool:
        return bool(
            self.structured_override_url
            and self.structured_override_api_key
            and self.structured_override_model
        )

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

    async def _post_structured_response_override(
        self,
        input_messages: list[dict[str, Any]],
        response_format: dict[str, Any],
        include_reasoning: bool,
        temperature: float,
        max_tokens: int,
    ) -> dict[str, Any]:
        if not self._can_use_structured_override():
            raise RuntimeError("Azure structured override is not configured")

        override_url = self.structured_override_url
        if not override_url:
            raise RuntimeError("Azure structured override URL is missing")

        payload: dict[str, Any] = {
            "model": self.structured_override_model,
            "input": input_messages,
            "max_output_tokens": max_tokens,
            "text": {
                "format": self._to_azure_text_format(response_format),
            },
        }

        if include_reasoning:
            payload["reasoning"] = {"summary": "auto"}
        if self._supports_temperature():
            payload["temperature"] = temperature

        headers = {
            "Content-Type": "application/json",
            "api-key": self.structured_override_api_key,
        }

        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0), headers=headers) as azure_client:
            try:
                response = await azure_client.post(override_url, json=payload)
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as exc:
                if self._is_temperature_unsupported(exc.response):
                    payload.pop("temperature", None)
                    retry_response = await azure_client.post(override_url, json=payload)
                    retry_response.raise_for_status()
                    return retry_response.json()
                raise

    def _extract_text_and_reasoning(self, data: dict[str, Any]) -> tuple[str, str]:
        """Extract response text and optional reasoning trace from /responses output.

        LiteLLM /responses format:
        {
            "output_text": "...",  # Primary text response
            "output": [            # Structured output items
                {"type": "message", "content": "..."},
                {"type": "text", "text": "..."},
                {"type": "reasoning", "content": "..."},
                ...
            ],
            "reasoning": "..."     # Top-level reasoning if requested
        }
        """
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
                if isinstance(content, str):
                    _append_text_candidate(content)
                elif isinstance(content, dict):
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

        text = "".join(text_parts).strip()
        reasoning = "".join(reasoning_parts).strip()

        if not text:
            error_payload = data.get("error")
            status = data.get("status")
            incomplete_details = data.get("incomplete_details")
            truncation = data.get("truncation")
            top_text = data.get("text")
            output_items = data.get("output") if isinstance(data.get("output"), list) else []

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
        max_tool_rounds: int = 4,
    ) -> dict[str, Any]:
        """Call LiteLLM /responses endpoint with optional tool calling loop.

        LiteLLM handles retries, fallbacks, and provider routing upstream.
        """
        # Check token usage before making the call
        self._check_token_usage(input_messages)

        merged_tools = self._resolve_request_tools(tools, response_format)

        if (
            response_format
            and not merged_tools
            and self._can_use_structured_override()
        ):
            return await self._post_structured_response_override(
                input_messages=input_messages,
                response_format=response_format,
                include_reasoning=include_reasoning,
                temperature=temperature,
                max_tokens=max_tokens,
            )

        payload: dict[str, Any] = {
            "model": self.model,
            "input": input_messages,
            "max_output_tokens": max_tokens,
        }

        if self.config.model_fallbacks:
            payload["fallbacks"] = self.config.model_fallbacks
        if response_format:
            payload["response_format"] = response_format
        if merged_tools:
            payload["tools"] = merged_tools
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

        # Tool calling loop (MCP tools)
        tool_round = 0
        tool_calls = self._extract_tool_calls(data)

        while tool_calls and tool_round < max_tool_rounds:
            logger.info("Executing %d MCP tool call(s) - round %d", len(tool_calls), tool_round + 1)
            assistant_tool_call_items: list[dict[str, Any]] = []
            tool_messages: list[dict[str, Any]] = []
            function_call_outputs: list[dict[str, Any]] = []

            for tool_call in tool_calls:
                result = await self._call_mcp_tool(tool_call)
                tool_call_type = tool_call.get("type")
                tool_call_id = tool_call.get("call_id") or tool_call.get("id") or tool_call.get("tool_call_id")
                tool_name = (
                    tool_call.get("name")
                    or tool_call.get("tool_name")
                    or tool_call.get("function", {}).get("name")
                )

                if tool_call_type == "function_call":
                    if tool_call_id:
                        function_call_outputs.append(
                            {
                                "type": "function_call_output",
                                "call_id": tool_call_id,
                                "output": json.dumps(result, default=str),
                            }
                        )
                    else:
                        logger.warning("Function call missing call_id; falling back to tool message format")

                if tool_call_type != "function_call" or not tool_call_id:
                    tool_message = {
                        "role": "tool",
                        "content": json.dumps(result, default=str),
                    }
                    if tool_call_id:
                        tool_message["tool_call_id"] = tool_call_id
                    if tool_name:
                        tool_message["name"] = tool_name
                    tool_messages.append(tool_message)

                if tool_call_type != "function_call":
                    assistant_tool_call_items.append(tool_call)

            # Add assistant response with tool calls + tool results
            if assistant_tool_call_items:
                input_messages.append({"role": "assistant", "content": assistant_tool_call_items})
            input_messages.extend(tool_messages)
            input_messages.extend(function_call_outputs)
            payload["input"] = input_messages

            # Next round
            response = await self.client.post(self.endpoint_url, json=payload)
            response.raise_for_status()
            data = response.json()
            tool_calls = self._extract_tool_calls(data)
            tool_round += 1

        if tool_calls:
            logger.warning("MCP tool call loop reached max rounds (%d)", max_tool_rounds)

        return data

    async def chat(
        self,
        messages: list[dict[str, str]],
        system_prompt: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        response_format: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
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
        response_format = _build_json_schema_format(DecisionResponse, "decision_response")

        response = await self.chat(messages, system_prompt, temperature=0.3, response_format=response_format)
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
        context = f"""
User Request: {user_input}

Decision: {decision}

Current Backlog: {current_backlog}

Generate a list of work items to fulfill this request.
"""
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
        context = f"""
Build Type: {item_type}
Description: {description}
Required Capabilities: {', '.join(capabilities)}

Generate a detailed specification for building this component.
"""
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
