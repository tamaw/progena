"""
OpenAI API Client for Progenitor.

Uses the OpenAI Responses API for all model calls.
Implements retry logic with exponential backoff for resilience.
Supports OpenAI and Azure OpenAI providers.
"""

import asyncio
import json
import logging
from typing import Any, Optional
from openai import APIConnectionError, APIError, RateLimitError, AsyncOpenAI

try:  # Optional dependency for Azure support
    from openai import AsyncAzureOpenAI
except ImportError:  # pragma: no cover - older SDKs
    AsyncAzureOpenAI = None  # type: ignore[assignment]

from .prompts import PromptTemplates
from ..config import LLMConfig

logger = logging.getLogger(__name__)


class LLMClient:
    """Wrapper for OpenAI API calls with retry logic."""

    DEFAULT_RETRIES = 3
    RETRY_DELAY = 1.0
    RETRY_MULTIPLIER = 2.0

    def __init__(
        self,
        config: Optional[LLMConfig] = None,
        max_retries: int = DEFAULT_RETRIES,
        role: Optional[str] = None,
    ):
        """Initialize the LLM client.

        Args:
            config: LLM configuration. If None, loads from environment.
            max_retries: Maximum retry attempts for API calls.
            role: Optional role for per-role Azure deployments (planner, builder, summary).
        """
        self.config = config or LLMConfig.from_env()
        self.max_retries = max_retries
        self.prompts = PromptTemplates()
        self.role = role

        if self.config.provider not in {"openai", "azure"}:
            raise ValueError(f"Unsupported LLM provider '{self.config.provider}'")

        if self.config.provider == "azure":
            self._configure_azure()
        else:
            self._configure_openai()

    def _configure_openai(self) -> None:
        """Configure OpenAI client."""
        if not self.config.openai_api_key:
            raise ValueError("OPENAI_API_KEY must be set for OpenAI provider")

        self.api_key = self.config.openai_api_key
        self.model = self.config.model_default
        self.client = AsyncOpenAI(api_key=self.api_key)

    def _configure_azure(self) -> None:
        """Configure Azure OpenAI client."""
        if not self.config.azure_endpoint:
            raise ValueError("AZURE_OPENAI_ENDPOINT must be set for Azure OpenAI provider")

        if AsyncAzureOpenAI is None:
            raise ImportError(
                "AsyncAzureOpenAI is not available in the installed openai package. "
                "Upgrade to openai>=1.0.0 to use Azure OpenAI."
            )

        # Try Azure key first, then fall back to OpenAI key
        key = self.config.azure_api_key or self.config.openai_api_key
        if not key:
            raise ValueError("AZURE_OPENAI_API_KEY (or OPENAI_API_KEY) must be set for Azure OpenAI provider")

        # Get deployment for role (falls back to default)
        deployment_name = self.config.get_deployment_for_role(self.role) if self.role else None
        deployment_name = deployment_name or self.config.azure_deployment_default

        if not deployment_name:
            raise ValueError("AZURE_OPENAI_DEPLOYMENT_DEFAULT must be set for Azure OpenAI provider")

        self.api_key = key
        self.model = deployment_name
        self.client = AsyncAzureOpenAI(
            api_key=self.api_key,
            azure_endpoint=self.config.azure_endpoint,
            api_version=self.config.azure_api_version,
        )

    async def _retry_with_backoff(self, func, *args, **kwargs) -> Any:
        """Execute function with exponential backoff retry.

        Retries on rate limits and connection errors. Does not retry
        on client errors (4xx except 429).
        """
        last_error: Optional[Exception] = None
        delay = self.RETRY_DELAY

        for attempt in range(self.max_retries):
            try:
                return await func(*args, **kwargs)
            except (RateLimitError, APIConnectionError) as e:
                last_error = e
                if attempt < self.max_retries - 1:
                    logger.warning(f"API call failed (attempt {attempt + 1}/{self.max_retries}): {e}. Retrying in {delay}s...")
                    await asyncio.sleep(delay)
                    delay *= self.RETRY_MULTIPLIER
            except APIError as e:
                # Don't retry on client errors (4xx except rate limit)
                status = getattr(e, 'status_code', None)
                if status and 400 <= status < 500 and status != 429:
                    logger.error(f"API client error (not retrying): {e}")
                    raise
                last_error = e
                if attempt < self.max_retries - 1:
                    logger.warning(f"API error (attempt {attempt + 1}/{self.max_retries}): {e}. Retrying in {delay}s...")
                    await asyncio.sleep(delay)
                    delay *= self.RETRY_MULTIPLIER

        logger.error(f"All {self.max_retries} retry attempts failed")
        if last_error:
            raise last_error
        raise RuntimeError("Retry failed with no captured error")

    async def chat(
        self,
        messages: list[dict[str, str]],
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        response_format: Optional[dict[str, Any]] = None,
    ) -> str:
        """Make a chat completion request with retry."""
        full_messages = []

        if system_prompt:
            full_messages.append({"role": "system", "content": system_prompt})

        full_messages.extend(messages)

        async def _do_chat():
            kwargs: dict[str, Any] = {
                "model": self.model,
                "messages": full_messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }

            if response_format:
                kwargs["response_format"] = response_format

            response = await self.client.chat.completions.create(**kwargs)
            return response.choices[0].message.content or ""

        return await self._retry_with_backoff(_do_chat)

    async def decide(
        self,
        user_input: str,
        conversation_history: list[dict[str, str]],
        available_capabilities: list[str],
    ) -> dict[str, Any]:
        """Make a routing decision for user input."""
        system_prompt = self.prompts.get_decide_prompt(available_capabilities)

        messages = conversation_history[-10:]  # Last 10 messages for context
        messages.append({"role": "user", "content": user_input})

        response = await self.chat(
            messages=messages,
            system_prompt=system_prompt,
            temperature=0.3,  # Lower temperature for decisions
            response_format={"type": "json_object"},
        )

        try:
            return json.loads(response)
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse decision JSON: {e}. Falling back to direct answer.")
            return {
                "action": "answer_directly",
                "reasoning": "JSON parse error - falling back to direct answer",
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

        response = await self.chat(
            messages=messages,
            system_prompt=system_prompt,
            temperature=0.5,
            response_format={"type": "json_object"},
        )

        try:
            result = json.loads(response)
            return result.get("work_items", [])
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse planner JSON: {e}. Returning empty work items.")
            return []

    async def generate_response(
        self,
        user_input: str,
        conversation_history: list[dict[str, str]],
        action_results: Optional[list[dict[str, Any]]] = None,
    ) -> str:
        """Generate a response to the user."""
        system_prompt = self.prompts.get_genesis_prompt()

        messages = conversation_history[-20:]  # More context for responses

        if action_results:
            results_summary = "\n".join([
                f"- {r.get('action', 'unknown')}: {r.get('result', 'completed')}"
                for r in action_results
            ])
            context_msg = f"Actions completed:\n{results_summary}\n\nRespond to the user about these results."
            messages.append({"role": "system", "content": context_msg})

        messages.append({"role": "user", "content": user_input})

        return await self.chat(
            messages=messages,
            system_prompt=system_prompt,
            temperature=0.7,
        )

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

        response = await self.chat(
            messages=messages,
            system_prompt=system_prompt,
            temperature=0.5,
            response_format={"type": "json_object"},
        )

        try:
            return json.loads(response)
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse build spec JSON: {e}")
            return {
                "name": "unnamed",
                "description": description,
                "kind": item_type,
                "capabilities": capabilities,
                "error": f"JSON parse error: {e}",
            }
