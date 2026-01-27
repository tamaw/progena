"""
OpenAI API Client for Progenitor.

Uses the OpenAI Responses API for all model calls.
Implements retry logic with exponential backoff and jitter for resilience.
Supports OpenAI and Azure OpenAI providers.
Includes telemetry for observability.
"""

import asyncio
import json
import logging
import random
import time
from typing import Any, AsyncIterator, Optional
from openai import APIConnectionError, APIError, RateLimitError, AsyncOpenAI

try:  # Optional dependency for Azure support
    from openai import AsyncAzureOpenAI
except ImportError:  # pragma: no cover - older SDKs
    AsyncAzureOpenAI = None  # type: ignore[assignment]

from .prompts import PromptTemplates
from ..config import LLMConfig

logger = logging.getLogger(__name__)


# Lazy telemetry imports to avoid circular dependencies
def _get_telemetry():
    """Get telemetry components if available."""
    try:
        from ..telemetry import get_metrics, get_tracer, SpanKind
        return get_metrics(), get_tracer(), SpanKind
    except ImportError:
        return None, None, None


class LLMClient:
    """Wrapper for OpenAI API calls with retry logic."""

    DEFAULT_RETRIES = 3
    RETRY_DELAY = 1.0
    RETRY_MULTIPLIER = 2.0
    RETRY_JITTER = 0.5  # ±50% jitter to prevent thundering herd

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
        """Execute function with exponential backoff retry and jitter.

        Retries on rate limits and connection errors. Does not retry
        on client errors (4xx except 429).

        Uses jitter to prevent thundering herd when multiple instances
        hit rate limits simultaneously.
        """
        last_error: Optional[Exception] = None
        delay = self.RETRY_DELAY

        for attempt in range(self.max_retries):
            try:
                return await func(*args, **kwargs)
            except (RateLimitError, APIConnectionError) as e:
                last_error = e
                if attempt < self.max_retries - 1:
                    # Add jitter: delay * (1 ± RETRY_JITTER)
                    jittered_delay = delay * (1.0 + random.uniform(-self.RETRY_JITTER, self.RETRY_JITTER))
                    logger.warning(
                        "API call failed (attempt %d/%d): %s. Retrying in %.1fs...",
                        attempt + 1, self.max_retries, e, jittered_delay
                    )
                    await asyncio.sleep(jittered_delay)
                    delay *= self.RETRY_MULTIPLIER
            except APIError as e:
                # Don't retry on client errors (4xx except rate limit)
                status = getattr(e, 'status_code', None)
                if status and 400 <= status < 500 and status != 429:
                    logger.error("API client error (not retrying): %s", e)
                    raise
                last_error = e
                if attempt < self.max_retries - 1:
                    jittered_delay = delay * (1.0 + random.uniform(-self.RETRY_JITTER, self.RETRY_JITTER))
                    logger.warning(
                        "API error (attempt %d/%d): %s. Retrying in %.1fs...",
                        attempt + 1, self.max_retries, e, jittered_delay
                    )
                    await asyncio.sleep(jittered_delay)
                    delay *= self.RETRY_MULTIPLIER

        logger.error("All %d retry attempts failed", self.max_retries)
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
        full_messages = self._build_messages(messages, system_prompt)

        # Telemetry setup
        metrics, tracer, SpanKind = _get_telemetry()
        span = None
        start_time = time.perf_counter()

        if tracer and SpanKind:
            span = tracer.start_span(
                "llm.chat",
                kind=SpanKind.CLIENT,
                attributes={
                    "llm.provider": self.config.provider,
                    "llm.model": self.model,
                    "llm.role": self.role,
                    "llm.message_count": len(full_messages),
                    "llm.max_tokens": max_tokens,
                    "llm.temperature": temperature,
                },
            )

        async def _do_chat():
            kwargs = self._build_chat_kwargs(full_messages, temperature, max_tokens, response_format)
            response = await self.client.chat.completions.create(**kwargs)

            # Record token usage in telemetry
            if metrics and hasattr(response, "usage") and response.usage:
                usage = response.usage
                if hasattr(usage, "prompt_tokens"):
                    metrics.llm_tokens_prompt.inc(usage.prompt_tokens)
                if hasattr(usage, "completion_tokens"):
                    metrics.llm_tokens_completion.inc(usage.completion_tokens)
                if span:
                    span.set_attribute("llm.prompt_tokens", getattr(usage, "prompt_tokens", 0))
                    span.set_attribute("llm.completion_tokens", getattr(usage, "completion_tokens", 0))

            return response.choices[0].message.content or ""

        try:
            result = await self._retry_with_backoff(_do_chat)

            # Record success metrics
            if metrics:
                metrics.llm_calls_total.inc()
                duration = time.perf_counter() - start_time
                metrics.llm_call_duration.observe(duration)

            if span:
                span.set_attribute("llm.success", True)
                span.set_attribute("llm.response_length", len(result))

            return result

        except Exception as e:
            if span:
                span.record_exception(e)
            raise

        finally:
            if span and tracer:
                tracer.end_span(span)

    def _build_messages(
        self,
        messages: list[dict[str, str]],
        system_prompt: Optional[str] = None,
    ) -> list[dict[str, str]]:
        """Build full message list with optional system prompt."""
        full_messages = []

        if system_prompt:
            full_messages.append({"role": "system", "content": system_prompt})

        full_messages.extend(messages)
        return full_messages

    def _build_chat_kwargs(
        self,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int,
        response_format: Optional[dict[str, Any]] = None,
        stream: bool = False,
    ) -> dict[str, Any]:
        """Build kwargs for chat completion API call."""
        adjusted_temp = temperature
        if self.config.provider == "azure" and temperature != 1.0:
            adjusted_temp = 1.0
            logger.debug(
                "Azure provider: adjusted temperature from %.2f to %.2f",
                temperature, adjusted_temp
            )

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": adjusted_temp,
            "stream": stream,
        }

        # Azure 2024-preview models reject max_tokens; use max_completion_tokens there
        if self.config.provider == "azure":
            kwargs["max_completion_tokens"] = max_tokens
        else:
            kwargs["max_tokens"] = max_tokens

        if response_format:
            kwargs["response_format"] = response_format

        return kwargs

    async def chat_stream(
        self,
        messages: list[dict[str, str]],
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> AsyncIterator[str]:
        """Make a streaming chat completion request.

        Yields content chunks as they arrive from the API.

        Args:
            messages: List of conversation messages.
            system_prompt: Optional system prompt.
            temperature: Sampling temperature.
            max_tokens: Maximum tokens in response.

        Yields:
            Content chunks as strings.
        """
        full_messages = self._build_messages(messages, system_prompt)

        # Telemetry setup
        metrics, tracer, SpanKind = _get_telemetry()
        span = None
        start_time = time.perf_counter()
        total_content = ""

        if tracer and SpanKind:
            span = tracer.start_span(
                "llm.chat_stream",
                kind=SpanKind.CLIENT,
                attributes={
                    "llm.provider": self.config.provider,
                    "llm.model": self.model,
                    "llm.role": self.role,
                    "llm.message_count": len(full_messages),
                    "llm.max_tokens": max_tokens,
                    "llm.temperature": temperature,
                    "llm.streaming": True,
                },
            )

        try:
            kwargs = self._build_chat_kwargs(
                full_messages, temperature, max_tokens, stream=True
            )

            # Note: streaming doesn't use retry_with_backoff since we can't
            # retry mid-stream. Connection errors will raise immediately.
            response = await self.client.chat.completions.create(**kwargs)

            async for chunk in response:
                if chunk.choices and chunk.choices[0].delta.content:
                    content = chunk.choices[0].delta.content
                    total_content += content
                    yield content

            # Record success metrics
            if metrics:
                metrics.llm_calls_total.inc()
                duration = time.perf_counter() - start_time
                metrics.llm_call_duration.observe(duration)

            if span:
                span.set_attribute("llm.success", True)
                span.set_attribute("llm.response_length", len(total_content))

        except Exception as e:
            if span:
                span.record_exception(e)
            raise

        finally:
            if span and tracer:
                tracer.end_span(span)

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

    async def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> str:
        """Generate a text response for a prompt.

        Simple wrapper around chat() for single-prompt generation.
        Used by planner, think, learn and other nodes.

        Args:
            prompt: The user prompt to generate a response for.
            system_prompt: Optional system prompt for context.
            temperature: Sampling temperature (0.0-1.0).
            max_tokens: Maximum tokens in response.

        Returns:
            Generated text response.
        """
        messages = [{"role": "user", "content": prompt}]
        return await self.chat(
            messages=messages,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
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
