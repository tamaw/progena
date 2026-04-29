"""
LangChain model adapter for Progenitor's direct Azure endpoint.

Routes all LLM calls through the configured Azure endpoint using LangChain's
ChatOpenAI, which is compatible with any OpenAI-compatible endpoint.
This preserves Progenitor's model routing and auth while enabling DeepAgent integration.

Supports optional extended thinking/reasoning for models that support it
(e.g., GPT-5.2-chat with reasoning enabled).
"""

import logging
from urllib.parse import urlsplit, urlunsplit

from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from ..config import LLMConfig

logger = logging.getLogger(__name__)


def _normalize_langchain_base_url(base_url: str) -> str:
    """Normalize env URL to OpenAI-compatible base URL for ChatOpenAI.

    Accepts either:
    - https://.../openai/v1
    - https://.../openai/v1/responses
    - https://.../openai/v1/responses?api-version=preview
    """
    raw = (base_url or "").strip()
    if not raw:
        return raw

    parsed = urlsplit(raw)
    path = (parsed.path or "").rstrip("/")
    if path.endswith("/responses"):
        path = path[: -len("/responses")]
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _resolve_reasoning_effort(config: LLMConfig) -> str:
    """Return the configured reasoning effort for the active model."""
    return config.reasoning_effort


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

    base_url = _normalize_langchain_base_url(config.base_url)

    # Azure's ChatOpenAI adapter exposes the final reasoning summary reliably
    # when effort is passed via `reasoning_effort` and summary mode rides
    # through `extra_body.reasoning.summary`.
    reasoning_effort: str | None = None
    extra_body: dict[str, dict[str, str]] | None = None
    if config.reasoning_enabled:
        reasoning_effort = _resolve_reasoning_effort(config)
        extra_body = {"reasoning": {"summary": config.reasoning_summary}}
        logger.info(
            "Reasoning enabled for model %s (effort=%s, summary=%s)",
            model_name,
            reasoning_effort,
            config.reasoning_summary,
        )

    model = ChatOpenAI(
        model=model_name,
        base_url=base_url,
        api_key=SecretStr(config.api_key or "not-needed"),
        default_headers={
            "api-key": config.api_key or "",
        },
        use_responses_api=True,
        output_version="responses/v1",
        streaming=True,
        reasoning_effort=reasoning_effort,
        extra_body=extra_body,
    )

    logger.info(
        "Created LangChain model adapter: model=%s, base_url=%s, role=%s, responses_api=%s, reasoning=%s",
        model_name,
        base_url,
        role,
        True,
        config.reasoning_enabled,
    )
    return model
