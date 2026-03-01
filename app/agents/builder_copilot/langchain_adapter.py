"""LangChain model adapter for Builder Copilot."""

from __future__ import annotations

import logging

from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from .config import LLMConfig

logger = logging.getLogger(__name__)


def create_langchain_model(
    config: LLMConfig | None = None,
    role: str | None = None,
) -> ChatOpenAI:
    """Create a LangChain ChatOpenAI client pointed at LiteLLM."""
    llm_config = config or LLMConfig.from_env()
    model_name = llm_config.get_model_for_role(role)
    base_url = llm_config.base_url.rstrip("/")

    model = ChatOpenAI(
        model=model_name,
        base_url=base_url,
        api_key=SecretStr(llm_config.api_key or "not-needed"),
        default_headers={"x-litellm-api-key": llm_config.api_key or ""},
        streaming=True,
    )
    logger.info("Created Builder Copilot LangChain model: %s", model_name)
    return model
