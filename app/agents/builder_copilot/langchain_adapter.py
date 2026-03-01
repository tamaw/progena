"""LangChain model adapter for Builder Copilot."""

from __future__ import annotations

import logging
from urllib.parse import urlsplit, urlunsplit

from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from .config import LLMConfig

logger = logging.getLogger(__name__)


def _normalize_langchain_base_url(base_url: str) -> str:
    """Normalize env URL to OpenAI-compatible base URL for ChatOpenAI."""
    raw = (base_url or "").strip()
    if not raw:
        return raw

    parsed = urlsplit(raw)
    path = (parsed.path or "").rstrip("/")
    if path.endswith("/responses"):
        path = path[:-len("/responses")]
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def create_langchain_model(
    config: LLMConfig | None = None,
    role: str | None = None,
) -> ChatOpenAI:
    """Create a LangChain ChatOpenAI client pointed at Azure Responses."""
    llm_config = config or LLMConfig.from_env()
    model_name = llm_config.get_model_for_role(role)
    base_url = _normalize_langchain_base_url(llm_config.base_url)

    model = ChatOpenAI(
        model=model_name,
        base_url=base_url,
        api_key=SecretStr(llm_config.api_key or "not-needed"),
        default_headers={"api-key": llm_config.api_key or ""},
        use_responses_api=True,
        output_version="responses/v1",
        streaming=True,
    )
    logger.info("Created Builder Copilot LangChain model: %s", model_name)
    return model
