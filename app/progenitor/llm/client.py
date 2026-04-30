"""Azure Responses API client for Progenitor."""

import json
import logging
from pathlib import Path
from typing import Any, TypeVar, cast
from urllib.parse import urlsplit, urlunsplit

import httpx
from langchain_core.tools import BaseTool
from pydantic import BaseModel

from ..config import LLMConfig, MCPConfig
from ..tools.user_communication import EventPusher
from .mcp_runtime import (
    call_mcp_tool,
    close_mcp_context,
    configure_mcp_context,
    get_mcp_tool_registry_entries,
    resolve_request_tools,
)
from .messages import build_input_messages, extract_text_and_reasoning
from .prompts import PromptTemplates
from .schemas import build_json_schema_format, to_azure_text_format
from .tool_calls import (
    build_tool_call_signature,
    build_tool_result_message,
    execute_system_or_agent_tool,
    extract_tool_calls,
    get_tool_call_id,
    get_tool_call_name,
    parse_tool_arguments,
)

logger = logging.getLogger(__name__)

TModel = TypeVar("TModel", bound=BaseModel)


def _build_responses_endpoint(base_url: str) -> str:
    """Build a valid /responses endpoint from either base URL or full endpoint URL."""
    raw = (base_url or "").strip()
    if not raw:
        return "/responses"

    parsed = urlsplit(raw)
    path = (parsed.path or "").rstrip("/")
    endpoint_path = path if path.endswith("/responses") else f"{path}/responses"

    return urlunsplit((parsed.scheme, parsed.netloc, endpoint_path, parsed.query, parsed.fragment))


class LLMClient:
    """Simple wrapper for Azure OpenAI /responses endpoint."""

    def __init__(
        self,
        config: LLMConfig | None = None,
        mcp_config: MCPConfig | None = None,
        prompts_dir: Path | str | None = None,
    ):
        """Initialize the LLM client.

        Args:
            config: LLM configuration. If None, loads from environment.
            mcp_config: MCP configuration. If None, loads from data/config/mcp.json.
            prompts_dir: Prompt root to use for template loading.
        """
        self.config = config or LLMConfig.from_env()
        self.mcp_config = mcp_config or MCPConfig.load()
        self.prompts = PromptTemplates(prompts_dir=prompts_dir)
        self.model = self.config.model_default

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
        self.mcp_servers: dict[str, dict[str, Any]] = {}
        self.mcp_client: httpx.AsyncClient | None = None
        self.mcp_tools: list[dict[str, Any]] = []
        self.mcp_langchain_tools: list[BaseTool] = []
        self.mcp_tool_lookup: dict[str, dict[str, Any]] = {}

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
        await self.close_mcp_context()

    async def close_mcp_context(self) -> None:
        """Close all initialized MCP clients and clear the active MCP context."""
        await close_mcp_context(mcp_servers=self.mcp_servers, mcp_client=self.mcp_client)
        self.mcp_servers = {}
        self.mcp_client = None
        self.mcp_tools = []
        self.mcp_langchain_tools = []
        self.mcp_tool_lookup = {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    def set_mcp_context(
        self,
        mcp_servers: dict[str, dict[str, Any]] | None,
        mcp_tools: list[dict[str, Any]] | None = None,
        mcp_langchain_tools: list[BaseTool] | None = None,
    ) -> None:
        """Set MCP server runtime metadata and tool list for tool calling."""
        (
            self.mcp_servers,
            self.mcp_client,
            self.mcp_tools,
            self.mcp_langchain_tools,
            self.mcp_tool_lookup,
        ) = configure_mcp_context(
            mcp_servers=mcp_servers,
            mcp_tools=mcp_tools,
            mcp_langchain_tools=mcp_langchain_tools,
        )

    def get_mcp_tool_registry_entries(self) -> list[dict[str, Any]]:
        """Return MCP tools formatted for tool registry storage."""
        return get_mcp_tool_registry_entries(self.mcp_tools)

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
        merged_tools = resolve_request_tools(
            mcp_tools=self.mcp_tools,
            tools=tools,
            response_format=response_format,
        )

        payload: dict[str, Any] = {
            "model": self.model,
            "input": input_messages,
            "max_output_tokens": max_tokens,
        }

        if response_format:
            payload["text"] = {"format": to_azure_text_format(response_format)}
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
        tool_calls = extract_tool_calls(data)
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
                tool_call_id = get_tool_call_id(tool_call)
                tool_name = get_tool_call_name(tool_call)
                tool_args = parse_tool_arguments(tool_call)
                sig = build_tool_call_signature(tool_name, tool_args)

                await self._push_event(
                    event_type="mcp_tool_call_started",
                    content=f"Calling tool: {tool_name}",
                    metadata={
                        "tool": tool_name,
                        "tool_call_id": tool_call_id,
                        "type": tool_call.get("type"),
                    },
                )

                result: dict[str, Any] | None
                if sig in seen_calls:
                    result = {"error": f"Repeated tool call detected: {tool_name}"}
                else:
                    seen_calls.add(sig)
                    result = None
                    if system_tool_context is not None:
                        result = await execute_system_or_agent_tool(
                            llm_client=self,
                            tool_name=tool_name,
                            tool_args=tool_args,
                            system_tool_context=system_tool_context,
                        )
                    if result is None:
                        result = await call_mcp_tool(
                            mcp_config=self.mcp_config,
                            mcp_servers=self.mcp_servers,
                            mcp_tools=self.mcp_tools,
                            mcp_tool_lookup=self.mcp_tool_lookup,
                            tool_call={**tool_call, "arguments": tool_args},
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

                tool_result_messages.append(
                    build_tool_result_message(
                        tool_name=tool_name,
                        tool_call_id=tool_call_id,
                        result=result or {},
                    )
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
            tool_calls = extract_tool_calls(data)
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
        prompt: str | list[dict[str, str]],
        system_prompt: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        response_format: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
        max_tool_rounds: int = 4,
        system_tool_context: dict[str, Any] | None = None,
    ) -> str:
        """Send a prompt or message history to /responses and return text.

        ``prompt`` may be a single user prompt string or a full conversation
        history as a list of ``{"role", "content"}`` messages.
        """
        if isinstance(prompt, str):
            messages: list[dict[str, str]] = [{"role": "user", "content": prompt}]
        else:
            messages = prompt

        if response_format:
            instruction = "Return only valid JSON that matches the provided schema. Do not include any extra text."
            system_prompt = f"{system_prompt}\n\n{instruction}" if system_prompt else instruction
        input_messages = build_input_messages(messages, system_prompt)

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
        text, _reasoning = extract_text_and_reasoning(data)
        return text

    async def chat_structured(
        self,
        prompt: str,
        response_model: type[TModel],
        schema_name: str,
        system_prompt: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> TModel:
        """Send a single-prompt request and return a validated structured model."""
        response_format = build_json_schema_format(response_model, schema_name)
        response = await self.chat(
            prompt=prompt,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
        )

        raw_data = json.loads(response)
        return response_model.model_validate(raw_data)
