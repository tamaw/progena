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

    def _format_input(self, messages: list[dict[str, str]], system_prompt: str | None = None) -> str:
        """Format messages into input text for /responses endpoint."""
        parts = []
        if system_prompt:
            parts.append(f"System: {system_prompt}")

        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role == "system":
                parts.append(f"System: {content}")
            elif role == "assistant":
                parts.append(f"Assistant: {content}")
            else:
                parts.append(f"User: {content}")

        return "\n\n".join(parts)

    def _extract_text_and_reasoning(self, data: dict[str, Any]) -> tuple[str, str]:
        """Extract response text and optional reasoning trace from /responses output."""
        text_parts: list[str] = []
        reasoning_parts: list[str] = []

        for item in data.get("output", []) or []:
            item_type = item.get("type")
            if item_type == "message":
                for part in item.get("content", []) or []:
                    part_type = part.get("type")
                    if part_type in ("output_text", "text"):
                        text_parts.append(part.get("text", ""))
                    elif part_type in ("reasoning", "reasoning_text", "reasoning_summary"):
                        reasoning_parts.append(part.get("text", ""))
            elif item_type in ("reasoning", "reasoning_text", "reasoning_summary"):
                if item.get("content"):
                    reasoning_parts.append(str(item.get("content")))
                elif item.get("text"):
                    reasoning_parts.append(str(item.get("text")))

        if data.get("reasoning"):
            reasoning_parts.append(str(data.get("reasoning")))

        return "".join(text_parts).strip(), "".join(reasoning_parts).strip()

    async def _post_response(
        self,
        input_text: str,
        response_format: dict[str, Any] | None = None,
        include_reasoning: bool = False,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "input": input_text,
        }
        if response_format:
            payload["response_format"] = response_format
        if include_reasoning:
            payload["reasoning"] = {"summary": "auto"}

        response = await self.client.post(self.endpoint_url, json=payload)
        response.raise_for_status()
        return response.json()

    async def chat(
        self,
        messages: list[dict[str, str]],
        system_prompt: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        response_format: dict[str, Any] | None = None,
    ) -> str:
        """Call /responses endpoint and return text response."""
        input_text = self._format_input(messages, system_prompt)

        data = await self._post_response(
            input_text=input_text,
            response_format=response_format,
            include_reasoning=False,
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
        input_text = self._format_input(messages, system_prompt)

        data = await self._post_response(
            input_text=input_text,
            response_format=response_format,
            include_reasoning=True,
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

        response = await self.chat(messages, system_prompt, temperature=0.3)

        try:
            raw_data = json.loads(response)
            validated = validate_decision_response(raw_data)
            return validated.model_dump()
        except Exception as e:
            logger.warning("Decision parsing failed: %s", e)
            return {
                "action": "answer_directly",
                "reasoning": f"Parse error: {e}",
                "requires_planning": False,
                "target_capabilities": [],
                "direct_response": response,
            }

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
        response = await self.chat(messages, system_prompt, temperature=0.5)

        try:
            raw_data = json.loads(response)
            validated = validate_planner_response(raw_data)
            return [item.model_dump() for item in validated.work_items]
        except Exception as e:
            logger.warning("Planner parsing failed: %s", e)
            return []

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
        response = await self.chat(messages, system_prompt, temperature=0.5)

        try:
            raw_data = json.loads(response)
            validated = validate_build_spec(raw_data)
            return validated.model_dump()
        except Exception as e:
            logger.warning("Build spec parsing failed: %s", e)
            return {
                "name": "unnamed",
                "description": description,
                "kind": item_type,
                "capabilities": capabilities,
                "entrypoint": "main.py",
                "dependencies": [],
                "config": {},
                "error": f"Parse error: {e}",
            }

    async def complete(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> str:
        """Complete a prompt. Alias for generate()."""
        return await self.generate(prompt, system_prompt, temperature, max_tokens)
