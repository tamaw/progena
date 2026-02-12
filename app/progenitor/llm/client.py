"""
LiteLLM API Client for Progenitor.

Uses the LiteLLM /responses endpoint for all model calls.
LiteLLM handles retries, provider routing, and observability upstream.
"""

import json
import logging
from collections.abc import AsyncIterator
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel

from ..config import LLMConfig, MCPConfig
from .prompts import PromptTemplates
from .tokenizer import count_message_tokens

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

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

        # TEMPORARY WORKAROUND:
        # LiteLLM structured output has been unreliable for some providers/models.
        # When enabled by env config, structured calls use Azure OpenAI /responses
        # directly so JSON schema outputs remain stable. Keep LiteLLM as default path
        # and for non-structured calls; this can be removed once LiteLLM is reliable.
        self.azure_structured_url = (self.config.azure_structured_responses_url or "").strip() or None
        self.azure_structured_enabled = bool(
            self.config.azure_structured_enabled
            and self.azure_structured_url
            and self.config.azure_structured_api_key
        )
        self.azure_structured_client: httpx.AsyncClient | None = None
        if self.azure_structured_enabled:
            self.azure_structured_client = httpx.AsyncClient(
                timeout=httpx.Timeout(120.0),
                headers={
                    "Content-Type": "application/json",
                    "api-key": str(self.config.azure_structured_api_key),
                },
            )
        elif self.config.azure_structured_enabled:
            logger.warning(
                "Azure structured output is enabled but config is incomplete; falling back to LiteLLM for structured calls"
            )

        # MCP context is initialized externally (startup)
        self.mcp_client: httpx.AsyncClient | None = None
        self.mcp_tools: list[dict[str, Any]] = []

        logger.info("LLM client configured (endpoint=%s, model=%s)", self.endpoint_url, self.model)

    async def close(self):
        """Close the HTTP client."""
        await self.client.aclose()
        if self.azure_structured_client:
            await self.azure_structured_client.aclose()
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

    def _extract_tool_calls(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        """Extract tool calls from a Responses API payload.

        Tool calls can appear in:
        - data["output"] as {"type": "tool_call", ...}
        - data["output"] > message > content as tool_call items
        - data["tool_calls"] as direct list
        """
        tool_calls: list[dict[str, Any]] = []

        # Check output array
        for item in data.get("output", []):
            if item.get("type") == "tool_call":
                tool_calls.append(item)
                continue

            # Check message content for tool calls
            if item.get("type") == "message":
                content = item.get("content")
                if isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict) and part.get("type") == "tool_call":
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
            "id": tool_call.get("id") or tool_call.get("tool_call_id") or 1,
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

    def _parse_structured_json(self, response_text: str) -> dict[str, Any]:
        """Parse structured JSON response with a bracketed-object fallback."""
        text = (response_text or "").strip()
        if not text:
            raise ValueError("Empty structured response")

        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}")
            if start == -1 or end == -1 or end <= start:
                raise
            payload = json.loads(text[start:end + 1])

        if not isinstance(payload, dict):
            raise ValueError(f"Expected JSON object, got {type(payload).__name__}")
        return payload

    def _get_structured_model_name(self) -> str:
        """Return model name for structured calls (Azure override if configured)."""
        return self.config.azure_structured_model or self.model

    async def _post_azure_structured_response(
        self,
        input_messages: list[dict[str, Any]],
        response_format: dict[str, Any],
        include_reasoning: bool,
        temperature: float,
        max_tokens: int,
    ) -> dict[str, Any]:
        """Call Azure OpenAI /responses directly for structured output.

        TEMPORARY WORKAROUND: direct Azure path for structured calls only.
        """
        if not self.azure_structured_client or not self.azure_structured_url:
            raise RuntimeError("Azure structured client is not configured")

        payload: dict[str, Any] = {
            "model": self._get_structured_model_name(),
            "input": input_messages,
            "max_output_tokens": max_tokens,
        }

        # TEMPORARY WORKAROUND TRANSLATION:
        # LiteLLM-style response_format uses:
        #   {"type":"json_schema", "json_schema":{"name":...,"schema":...,"strict":...}}
        # Azure OpenAI Responses expects text.format flattened as:
        #   {"type":"json_schema", "name":..., "schema":..., "strict":...}
        # Keep this translation local to the direct-Azure structured path so it
        # can be removed once LiteLLM structured output is reliable again.
        azure_text_format: dict[str, Any]
        if response_format.get("type") == "json_schema" and isinstance(response_format.get("json_schema"), dict):
            json_schema = response_format["json_schema"]
            azure_text_format = {
                "type": "json_schema",
                "name": json_schema.get("name", "structured_response"),
                "schema": json_schema.get("schema", {}),
                "strict": bool(json_schema.get("strict", True)),
            }
        else:
            azure_text_format = response_format

        payload["text"] = {"format": azure_text_format}
        if include_reasoning:
            payload["reasoning"] = {"summary": "auto"}
        if self._supports_temperature():
            payload["temperature"] = temperature

        try:
            response = await self.azure_structured_client.post(self.azure_structured_url, json=payload)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            if self._is_temperature_unsupported(exc.response):
                logger.debug("Azure structured call: temperature unsupported, retrying without it")
                payload.pop("temperature", None)
                response = await self.azure_structured_client.post(self.azure_structured_url, json=payload)
                response.raise_for_status()
                return response.json()
            raise

    async def chat_structured(
        self,
        messages: list[dict[str, str]],
        response_model: type[T],
        schema_name: str,
        system_prompt: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tools: list[dict[str, Any]] | None = None,
    ) -> T:
        """Call chat() with structured output and return validated model."""
        response_format = self._get_json_response_format(response_model, schema_name)

        if response_format.get("type") == "json_object":
            instruction = "Return a single JSON object and nothing else (valid json)."
            system_prompt = f"{system_prompt}\n\n{instruction}" if system_prompt else instruction

        if self.azure_structured_enabled:
            input_messages = self._build_input_messages(messages, system_prompt)
            data = await self._post_azure_structured_response(
                input_messages=input_messages,
                response_format=response_format,
                include_reasoning=False,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            response_text, _reasoning = self._extract_text_and_reasoning(data)
        else:
            response_text = await self.chat(
                messages=messages,
                system_prompt=system_prompt,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format=response_format,
                tools=tools,
            )
        payload = self._parse_structured_json(response_text)
        return response_model.model_validate(payload)

    async def chat_structured_with_reasoning(
        self,
        messages: list[dict[str, str]],
        response_model: type[T],
        schema_name: str,
        system_prompt: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tools: list[dict[str, Any]] | None = None,
    ) -> tuple[T, str]:
        """Call chat_with_reasoning() with structured output and return validated model."""
        response_format = self._get_json_response_format(response_model, schema_name)

        if response_format.get("type") == "json_object":
            instruction = "Return a single JSON object and nothing else (valid json)."
            system_prompt = f"{system_prompt}\n\n{instruction}" if system_prompt else instruction

        if self.azure_structured_enabled:
            input_messages = self._build_input_messages(messages, system_prompt)
            data = await self._post_azure_structured_response(
                input_messages=input_messages,
                response_format=response_format,
                include_reasoning=True,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            response_text, reasoning_trace = self._extract_text_and_reasoning(data)
        else:
            response_text, reasoning_trace = await self.chat_with_reasoning(
                messages=messages,
                system_prompt=system_prompt,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format=response_format,
                tools=tools,
            )
        payload = self._parse_structured_json(response_text)
        return response_model.model_validate(payload), reasoning_trace

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

        # Primary text response
        if output_text := data.get("output_text"):
            text_parts.append(str(output_text))

        # Structured output items
        for item in data.get("output", []):
            item_type = item.get("type")

            if item_type == "message":
                content = item.get("content", "")
                if isinstance(content, str):
                    text_parts.append(content)
                elif isinstance(content, dict) and content.get("text"):
                    text_parts.append(str(content["text"]))
                elif isinstance(content, list):
                    for part in content:
                        if not isinstance(part, dict):
                            continue
                        part_type = part.get("type")
                        if part_type in ("output_text", "text"):
                            text_parts.append(part.get("text", ""))
                        elif part_type == "refusal":
                            text_parts.append(part.get("refusal") or part.get("text", ""))
                        elif part_type in ("reasoning", "reasoning_text", "reasoning_summary"):
                            reasoning_parts.append(part.get("text", ""))

            elif item_type in ("output_text", "text"):
                text_parts.append(str(item.get("text", "")))

            elif item_type in ("reasoning", "reasoning_text", "reasoning_summary"):
                reasoning_parts.append(str(item.get("content") or item.get("text", "")))

            elif item_type == "refusal":
                text_parts.append(str(item.get("refusal") or item.get("text", "")))

        # Top-level reasoning
        if reasoning := data.get("reasoning"):
            reasoning_parts.append(str(reasoning))

        text = "".join(text_parts).strip()
        reasoning = "".join(reasoning_parts).strip()

        if not text:
            logger.warning("Empty LLM response - keys present: %s", list(data.keys()))

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

        merged_tools = self._merge_tools(tools)

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
            tool_messages: list[dict[str, Any]] = []

            for tool_call in tool_calls:
                result = await self._call_mcp_tool(tool_call)
                tool_message = {
                    "role": "tool",
                    "content": json.dumps(result, default=str),
                }
                if tool_call_id := tool_call.get("id") or tool_call.get("tool_call_id"):
                    tool_message["tool_call_id"] = tool_call_id
                if tool_name := tool_call.get("name") or tool_call.get("tool_name"):
                    tool_message["name"] = tool_name
                tool_messages.append(tool_message)

            # Add assistant response with tool calls + tool results
            input_messages.append({"role": "assistant", "content": tool_calls})
            input_messages.extend(tool_messages)
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
    ) -> AsyncIterator[str]:
        """Stream not supported, returns full response as single chunk."""
        response = await self.chat(messages, system_prompt, temperature, max_tokens)
        yield response

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
        response_format: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> str:
        """Generate text response for a prompt."""
        messages = [{"role": "user", "content": prompt}]
        return await self.chat(
            messages,
            system_prompt,
            temperature,
            max_tokens,
            response_format=response_format,
            tools=tools,
        )

    async def generate_with_reasoning(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        response_format: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> tuple[str, str]:
        """Generate text response and return reasoning trace when available."""
        messages = [{"role": "user", "content": prompt}]
        return await self.chat_with_reasoning(
            messages,
            system_prompt,
            temperature,
            max_tokens,
            response_format=response_format,
            tools=tools,
        )

    async def complete(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        response_format: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> str:
        """Complete a prompt. Alias for generate()."""
        return await self.generate(
            prompt,
            system_prompt,
            temperature,
            max_tokens,
            response_format=response_format,
            tools=tools,
        )
