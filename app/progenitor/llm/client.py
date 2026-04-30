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
from .messages import build_input_messages, extract_text_and_reasoning
from .prompts import PromptTemplates
from .schemas import build_json_schema_format, to_azure_text_format

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

        # MCP context is initialized externally and executed by DeepAgent tools.
        self.mcp_langchain_tools: list[BaseTool] = []
        self.mcp_tool_entries: list[dict[str, Any]] = []

        logger.info("LLM client configured (endpoint=%s, model=%s)", self.endpoint_url, self.model)

    async def close(self):
        """Close the HTTP client."""
        await self.client.aclose()
        await self.close_mcp_context()

    async def close_mcp_context(self) -> None:
        """Clear the active MCP context."""
        self.mcp_langchain_tools = []
        self.mcp_tool_entries = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    def set_mcp_context(
        self,
        mcp_langchain_tools: list[BaseTool] | None = None,
        mcp_tool_entries: list[dict[str, Any]] | None = None,
    ) -> None:
        """Set adapter-backed MCP tools and registry metadata."""
        self.mcp_langchain_tools = mcp_langchain_tools or []
        self.mcp_tool_entries = mcp_tool_entries or []

    def get_mcp_tool_registry_entries(self) -> list[dict[str, Any]]:
        """Return MCP tools formatted for tool registry storage."""
        return list(self.mcp_tool_entries)

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
    ) -> dict[str, Any]:
        """Call /responses endpoint."""
        payload: dict[str, Any] = {
            "model": self.model,
            "input": input_messages,
            "max_output_tokens": max_tokens,
        }

        if response_format:
            payload["text"] = {"format": to_azure_text_format(response_format)}
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

        return cast(dict[str, Any], data)

    async def chat(
        self,
        prompt: str | list[dict[str, str]],
        system_prompt: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        response_format: dict[str, Any] | None = None,
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
