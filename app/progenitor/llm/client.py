"""
OpenAI API Client for Progenitor.

Uses the OpenAI Responses API for all model calls.
Implements retry logic with exponential backoff and jitter for resilience.
Supports OpenAI and Azure OpenAI providers.
Includes telemetry for observability and Langfuse for LLM tracing.
"""

import asyncio
import json
import logging
import random
import time
from typing import Any, AsyncIterator, Optional
from openai import APIConnectionError, APIError, RateLimitError

# Import clients from observability module (Langfuse-wrapped if configured)
from ..observability import get_openai_client, get_azure_openai_client, get_langfuse_config

from .prompts import PromptTemplates
from .response_models import (
    validate_decision_response,
    validate_planner_response,
    validate_build_spec,
    DecisionResponse,
    PlannerResponse,
    BuildSpec,
)
from ..config import LLMConfig

logger = logging.getLogger(__name__)


# Minimum Azure API version that supports json_schema response format
MIN_AZURE_API_VERSION_FOR_JSON_SCHEMA = "2024-08-01-preview"


def _azure_supports_json_schema(api_version: str) -> bool:
    """Check if the Azure API version supports json_schema response format.

    json_schema structured outputs require api version 2024-08-01-preview or later.
    """
    # Extract date components for comparison
    # Format: YYYY-MM-DD or YYYY-MM-DD-preview
    try:
        version_date = api_version.split("-preview")[0]  # Remove -preview suffix
        min_date = MIN_AZURE_API_VERSION_FOR_JSON_SCHEMA.split("-preview")[0]
        return version_date >= min_date
    except Exception:
        # If parsing fails, assume it doesn't support
        return False


def _build_json_schema_format(model_class: type, name: str) -> dict[str, Any]:
    """Build structured output response_format from a Pydantic model.

    Uses json_schema response format which doesn't require "json" in the prompt,
    unlike the json_object response format.

    Args:
        model_class: A Pydantic BaseModel class.
        name: Schema name for the response format.

    Returns:
        A dict suitable for response_format parameter.
    """
    schema = model_class.model_json_schema()

    # OpenAI structured outputs require additionalProperties: false at all levels
    # and all properties must be required
    def _enforce_strict_schema(obj: dict) -> dict:
        """Recursively enforce strict schema rules for OpenAI structured outputs."""
        if not isinstance(obj, dict):
            return obj

        result = dict(obj)

        # For object types, add additionalProperties: false and make all required
        if result.get("type") == "object" and "properties" in result:
            result["additionalProperties"] = False
            # Make all properties required
            result["required"] = list(result["properties"].keys())
            # Recursively process nested properties
            for prop_name, prop_schema in result["properties"].items():
                result["properties"][prop_name] = _enforce_strict_schema(prop_schema)

        # Process array items
        if "items" in result:
            result["items"] = _enforce_strict_schema(result["items"])

        # Process $defs (definitions)
        if "$defs" in result:
            result["$defs"] = {
                k: _enforce_strict_schema(v) for k, v in result["$defs"].items()
            }

        # Process allOf, anyOf, oneOf
        for key in ("allOf", "anyOf", "oneOf"):
            if key in result:
                result[key] = [_enforce_strict_schema(item) for item in result[key]]

        return result

    strict_schema = _enforce_strict_schema(schema)

    return {
        "type": "json_schema",
        "json_schema": {
            "name": name,
            "strict": True,
            "schema": strict_schema,
        },
    }


# Lazy telemetry imports to avoid circular dependencies
def _get_telemetry():
    """Get telemetry components if available."""
    try:
        from ..telemetry import get_metrics, get_tracer, SpanKind
        return get_metrics(), get_tracer(), SpanKind
    except ImportError:
        return None, None, None


class LLMClient:
    """Wrapper for OpenAI API calls with retry logic and Langfuse observability."""

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
        """Configure OpenAI client with Langfuse observability if available."""
        if not self.config.openai_api_key:
            raise ValueError("OPENAI_API_KEY must be set for OpenAI provider")

        self.api_key = self.config.openai_api_key
        self.model = self.config.model_default

        # Use Langfuse-wrapped client if configured
        self.client = get_openai_client(async_client=True)
        # Set API key (Langfuse client reads from env, but we ensure it's set)
        self.client.api_key = self.api_key

        langfuse_config = get_langfuse_config()
        if langfuse_config.is_configured():
            logger.info("OpenAI client configured with Langfuse observability")

    def _configure_azure(self) -> None:
        """Configure Azure OpenAI client with Langfuse observability if available."""
        if not self.config.azure_endpoint:
            raise ValueError("AZURE_OPENAI_ENDPOINT must be set for Azure OpenAI provider")

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

        # Use Langfuse-wrapped Azure client if configured
        self.client = get_azure_openai_client(
            azure_endpoint=self.config.azure_endpoint,
            api_key=self.api_key,
            api_version=self.config.azure_api_version,
            async_client=True,
        )

        langfuse_config = get_langfuse_config()
        if langfuse_config.is_configured():
            logger.info("Azure OpenAI client configured with Langfuse observability")

    def _get_json_response_format(
        self,
        model_class: type,
        schema_name: str,
    ) -> dict[str, Any]:
        """Get the appropriate JSON response format based on provider and API version.

        Uses json_schema (structured outputs) when supported, falls back to json_object.

        Args:
            model_class: Pydantic model class for structured outputs.
            schema_name: Name for the JSON schema.

        Returns:
            Response format dict for the API call.
        """
        # OpenAI always supports json_schema
        if self.config.provider == "openai":
            return _build_json_schema_format(model_class, schema_name)

        # Azure: check if API version supports json_schema
        if self.config.provider == "azure":
            if _azure_supports_json_schema(self.config.azure_api_version):
                return _build_json_schema_format(model_class, schema_name)
            else:
                # Fall back to json_object (requires "json" in prompt)
                logger.debug(
                    "Azure API version %s doesn't support json_schema, using json_object",
                    self.config.azure_api_version,
                )
                return {"type": "json_object"}

        # Default to json_object for unknown providers
        return {"type": "json_object"}

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
        """Make a routing decision for user input.

        Returns validated decision response with guaranteed schema.
        Uses structured outputs (json_schema) when supported, falls back to json_object.
        """
        system_prompt = self.prompts.get_decide_prompt(available_capabilities)

        messages = conversation_history[-10:]  # Last 10 messages for context
        messages.append({"role": "user", "content": user_input})

        # Choose response format based on provider and API version support
        response_format = self._get_json_response_format(DecisionResponse, "decision_response")

        # If using json_object fallback, add json instruction to prompt
        if response_format.get("type") == "json_object":
            system_prompt += "\n\nReturn a single JSON object and nothing else (valid json)."

        response = await self.chat(
            messages=messages,
            system_prompt=system_prompt,
            temperature=0.3,  # Lower temperature for decisions
            response_format=response_format,
        )

        try:
            raw_data = json.loads(response)
            # Validate with Pydantic model - guarantees schema compliance
            validated = validate_decision_response(raw_data)
            return validated.model_dump()
        except json.JSONDecodeError as e:
            logger.warning("Failed to parse decision JSON: %s. Falling back to direct answer.", e)
            return {
                "action": "answer_directly",
                "reasoning": f"JSON parse error - falling back to direct answer: {e}",
                "requires_planning": False,
                "target_capabilities": [],
                "direct_response": response,
            }
        except Exception as e:
            logger.warning("Decision validation failed: %s. Using defaults.", e)
            return {
                "action": "answer_directly",
                "reasoning": f"Validation error - falling back to direct answer: {e}",
                "requires_planning": False,
                "target_capabilities": [],
                "direct_response": None,
            }

    async def plan(
        self,
        user_input: str,
        decision: dict[str, Any],
        current_backlog: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Generate work items for a task.

        Returns validated work items with guaranteed schema.
        Uses structured outputs (json_schema) to avoid 'must contain json' constraint.
        """
        system_prompt = self.prompts.get_planner_prompt()

        context = f"""
User Request: {user_input}

Decision: {decision}

Current Backlog: {current_backlog}

Generate a list of work items to fulfill this request.
"""

        messages = [{"role": "user", "content": context}]

        # Choose response format based on provider and API version support
        response_format = self._get_json_response_format(PlannerResponse, "planner_response")

        # If using json_object fallback, add json instruction to prompt
        if response_format.get("type") == "json_object":
            system_prompt += "\n\nReturn a single JSON object and nothing else (valid json)."

        response = await self.chat(
            messages=messages,
            system_prompt=system_prompt,
            temperature=0.5,
            response_format=response_format,
        )

        try:
            raw_data = json.loads(response)
            # Validate with Pydantic model
            validated = validate_planner_response(raw_data)
            return [item.model_dump() for item in validated.work_items]
        except json.JSONDecodeError as e:
            logger.warning("Failed to parse planner JSON: %s. Returning empty work items.", e)
            return []
        except Exception as e:
            logger.warning("Planner validation failed: %s. Returning empty work items.", e)
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
        """Generate a specification for building an agent or MCP server.

        Returns validated build spec with guaranteed schema.
        Uses structured outputs (json_schema) to avoid 'must contain json' constraint.
        """
        system_prompt = self.prompts.get_build_prompt(item_type)

        context = f"""
Build Type: {item_type}
Description: {description}
Required Capabilities: {', '.join(capabilities)}

Generate a detailed specification for building this component.
"""

        messages = [{"role": "user", "content": context}]

        # Choose response format based on provider and API version support
        response_format = self._get_json_response_format(BuildSpec, "build_spec")

        # If using json_object fallback, add json instruction to prompt
        if response_format.get("type") == "json_object":
            system_prompt += "\n\nReturn a single JSON object and nothing else (valid json)."

        response = await self.chat(
            messages=messages,
            system_prompt=system_prompt,
            temperature=0.5,
            response_format=response_format,
        )

        try:
            raw_data = json.loads(response)
            # Validate with Pydantic model
            validated = validate_build_spec(raw_data)
            return validated.model_dump()
        except json.JSONDecodeError as e:
            logger.warning("Failed to parse build spec JSON: %s", e)
            return {
                "name": "unnamed",
                "description": description,
                "kind": item_type,
                "capabilities": capabilities,
                "entrypoint": "main.py",
                "dependencies": [],
                "config": {},
                "error": f"JSON parse error: {e}",
            }
        except Exception as e:
            logger.warning("Build spec validation failed: %s", e)
            return {
                "name": "unnamed",
                "description": description,
                "kind": item_type,
                "capabilities": capabilities,
                "entrypoint": "main.py",
                "dependencies": [],
                "config": {},
                "error": f"Validation error: {e}",
            }
