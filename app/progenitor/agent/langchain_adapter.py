"""
LangChain model adapter for Progenitor's LiteLLM proxy.

Routes all LLM calls through the existing LiteLLM proxy using LangChain's
ChatOpenAI, which is compatible with any OpenAI-compatible endpoint.
This preserves Progenitor's model routing, auth, and observability (Langfuse)
while enabling DeepAgent integration.
"""

import logging
import os

from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from ..config import LLMConfig

logger = logging.getLogger(__name__)


def create_langchain_model(
    config: LLMConfig | None = None,
    role: str | None = None,
) -> ChatOpenAI:
    """Create a LangChain ChatOpenAI instance routed through LiteLLM.

    Args:
        config: LLM configuration. If None, loads from environment.
        role: Optional role for model selection (planner, builder, summary).
              Uses role-specific model if configured, otherwise default.

    Returns:
        ChatOpenAI instance pointing at LiteLLM proxy.
    """
    if config is None:
        config = LLMConfig.from_env()

    model_name = config.get_model_for_role(role) if role else config.model_default

    # LiteLLM exposes an OpenAI-compatible API, so ChatOpenAI works directly.
    # The /v1 path is appended automatically by ChatOpenAI.
    base_url = config.base_url.rstrip("/")

    model = ChatOpenAI(
        model=model_name,
        base_url=base_url,
        api_key=SecretStr(config.api_key or "not-needed"),
        default_headers={
            "x-litellm-api-key": config.api_key or "",
        },
        streaming=True,
    )

    logger.info(
        "Created LangChain model adapter: model=%s, base_url=%s, role=%s",
        model_name,
        base_url,
        role,
    )
    return model


def create_azure_structured_model() -> ChatOpenAI | None:
    """Create a direct Azure model for structured output (bypass LiteLLM).

    Some Azure deployments have issues with structured output through LiteLLM.
    This creates a direct connection for structured output calls only.

    Returns:
        ChatOpenAI instance for Azure structured output, or None if not configured.
    """
    url = os.getenv("PROGENITOR_AZURE_STRUCTURED_RESPONSES_URL")
    api_key = os.getenv("PROGENITOR_AZURE_STRUCTURED_API_KEY")
    model_name = os.getenv("PROGENITOR_AZURE_STRUCTURED_MODEL")

    if not all([url, api_key, model_name]):
        return None

    model = ChatOpenAI(
        model=model_name or "gpt-4o",
        base_url=url,
        api_key=SecretStr(api_key) if api_key else None,
    )

    logger.info("Created Azure structured output model: model=%s", model_name)
    return model
