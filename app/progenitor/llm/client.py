"""
LiteLLM API Client for Progenitor.

Uses the LiteLLM /responses endpoint for all model calls.
LiteLLM handles retries, provider routing, and observability upstream.
"""

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

from ..config import LLMConfig
from .prompts import PromptTemplates
from .response_models import (
    BuildSpec,
    DecisionResponse,
    PlannerResponse,
    validate_build_spec,
    validate_decision_response,
    validate_planner_response,
)

logger = logging.getLogger(__name__)


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
        max_retries: int = 3,  # Kept for compatibility but unused
        role: str | None = None,
    ):
        """Initialize the LLM client.

        Args:
            config: LLM configuration. If None, loads from environment.
            max_retries: Unused - LiteLLM handles retries.
            role: Optional role for per-role model selection (planner, builder, summary).
        """
        self.config = config or LLMConfig.from_env()
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

        logger.info("LLM client configured (endpoint=%s, model=%s)", self.endpoint_url, self.model)

    async def close(self):
        """Close the HTTP client."""
        await self.client.aclose()

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

    def _get_json_response_format(self, response_model: type, schema_name: str) -> dict[str, Any]:
        """Return a response_format payload for structured output."""
        return _build_json_schema_format(response_model, schema_name)

    def _extract_text_and_reasoning(self, data: dict[str, Any]) -> tuple[str, str]:
        """Extract response text and optional reasoning trace from /responses output."""
        text_parts: list[str] = []
        reasoning_parts: list[str] = []

        output_text = data.get("output_text")
        if isinstance(output_text, str) and output_text.strip():
            text_parts.append(output_text)

        for item in data.get("output", []) or []:
            item_type = item.get("type")
            if item_type == "message":
                content = item.get("content")
                if isinstance(content, str):
                    text_parts.append(content)
                elif isinstance(content, dict):
                    if content.get("text"):
                        text_parts.append(str(content.get("text")))
                else:
                    for part in content or []:
                        part_type = part.get("type") if isinstance(part, dict) else None
                        if part_type in ("output_text", "text"):
                            text_parts.append(part.get("text", ""))
                        elif part_type == "refusal":
                            text_parts.append(part.get("refusal", part.get("text", "")))
                        elif part_type in ("reasoning", "reasoning_text", "reasoning_summary"):
                            reasoning_parts.append(part.get("text", ""))
            elif item_type in ("output_text", "text"):
                text_parts.append(str(item.get("text", "")))
            elif item_type in ("reasoning", "reasoning_text", "reasoning_summary"):
                if item.get("content"):
                    reasoning_parts.append(str(item.get("content")))
                elif item.get("text"):
                    reasoning_parts.append(str(item.get("text")))
            elif item_type == "refusal":
                if item.get("refusal"):
                    text_parts.append(str(item.get("refusal")))
                elif item.get("text"):
                    text_parts.append(str(item.get("text")))
            elif item.get("text"):
                text_parts.append(str(item.get("text")))

        if data.get("reasoning"):
            reasoning_parts.append(str(data.get("reasoning")))

        text = "".join(text_parts).strip()
        reasoning = "".join(reasoning_parts).strip()

        if not text:
            logger.warning("Empty LLM response text; keys=%s", list(data.keys()))

        return text, reasoning

    def _supports_temperature(self) -> bool:
        """Return True if temperature should be sent for the current model."""
        unsupported = {"gpt-5.2-chat"}
        model_lower = self.model.lower()
        return not any(token in model_lower for token in unsupported)

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
        payload: dict[str, Any] = {
            "model": self.model,
            "input": input_messages,
            "max_output_tokens": max_tokens,
        }
        if self.config.model_fallbacks:
            payload["fallbacks"] = self.config.model_fallbacks
        if response_format:
            payload["response_format"] = response_format
        if include_reasoning:
            payload["reasoning"] = {"summary": "auto"}
        if self._supports_temperature():
            payload["temperature"] = temperature

        try:
            response = await self.client.post(self.endpoint_url, json=payload)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            if self._is_temperature_unsupported(exc.response) and "temperature" in payload:
                payload.pop("temperature", None)
                retry = await self.client.post(self.endpoint_url, json=payload)
                retry.raise_for_status()
                return retry.json()
            raise

    async def chat(
        self,
        messages: list[dict[str, str]],
        system_prompt: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        response_format: dict[str, Any] | None = None,
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
    ) -> tuple[str, str]:
        """Call /responses endpoint and return text plus reasoning trace."""
        input_messages = self._build_input_messages(messages, system_prompt)

        data = await self._post_response(
            input_messages=input_messages,
            response_format=response_format,
            include_reasoning=True,
            temperature=temperature,
            max_tokens=max_tokens,
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
