"""
LangChain model adapter for Progenitor's direct Azure endpoint.

Routes all LLM calls through the configured Azure endpoint using LangChain's
ChatOpenAI, which is compatible with any OpenAI-compatible endpoint.
This preserves Progenitor's model routing and auth while enabling DeepAgent integration.

Supports optional extended thinking/reasoning for models that support it
(e.g., GPT-5.2-chat with reasoning enabled).
"""

import logging
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from ..config import LLMConfig

logger = logging.getLogger(__name__)


def create_langchain_model(
    config: LLMConfig | None = None,
    role: str | None = None,
) -> ChatOpenAI:
    """Create a LangChain ChatOpenAI instance routed through Azure.

    Args:
        config: LLM configuration. If None, loads from environment.
        role: Optional role for model selection (planner, builder, summary).
              Uses role-specific model if configured, otherwise default.

    Returns:
        ChatOpenAI instance pointing at configured Azure endpoint.
    """
    if config is None:
        config = LLMConfig.from_env()

    model_name = config.get_model_for_role(role) if role else config.model_default

    base_url = config.base_url.rstrip("/")

    # Build model kwargs for extended reasoning if enabled.
    reasoning_effort: str | None = None
    if config.reasoning_enabled:
        budget = config.reasoning_budget_tokens
        if budget is not None:
            if budget <= 1024:
                reasoning_effort = "low"
            elif budget <= 4096:
                reasoning_effort = "medium"
            else:
                reasoning_effort = "high"
        else:
            reasoning_effort = "medium"
        logger.info(
            "Reasoning enabled for model %s (effort=%s, budget_tokens=%s)",
            model_name,
            reasoning_effort,
            config.reasoning_budget_tokens or "default",
        )

    model = ChatOpenAI(
        model=model_name,
        base_url=base_url,
        api_key=SecretStr(config.api_key or "not-needed"),
        default_headers={
            "api-key": config.api_key or "",
        },
        streaming=True,
        reasoning_effort=reasoning_effort,
    )

    logger.info(
        "Created LangChain model adapter: model=%s, base_url=%s, role=%s, reasoning=%s",
        model_name,
        base_url,
        role,
        config.reasoning_enabled,
    )
    return model
