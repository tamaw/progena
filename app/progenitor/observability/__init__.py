"""
Progenitor Observability - Langfuse Integration.

Provides LLM observability through Langfuse including:
- Automatic tracing of all LLM calls
- Token usage and cost tracking
- Latency monitoring
- Prompt/completion logging
- Session and user tracking

Configuration:
    LANGFUSE_ENABLED: Enable/disable Langfuse (default: true if keys present)
    LANGFUSE_PUBLIC_KEY: Langfuse public key
    LANGFUSE_SECRET_KEY: Langfuse secret key
    LANGFUSE_HOST: Langfuse server URL (default: https://cloud.langfuse.com)
"""

from .config import LangfuseConfig, get_langfuse_config
from .client import (
    get_langfuse_client,
    get_openai_client,
    get_azure_openai_client,
    flush_langfuse,
    shutdown_langfuse,
    observe,
)
from .structured_logging import (
    get_trace_id,
    set_trace_id,
    get_request_id,
    set_request_id,
    generate_trace_id,
    generate_request_id,
    get_structured_logger,
    LogContext,
    log_info,
    log_warning,
    log_error,
    log_debug,
    JSONFormatter,
    setup_json_logging,
)

__all__ = [
    # Config
    "LangfuseConfig",
    "get_langfuse_config",
    # Client
    "get_langfuse_client",
    "get_openai_client",
    "get_azure_openai_client",
    "flush_langfuse",
    "shutdown_langfuse",
    "observe",
    # Structured Logging (OBS-003)
    "get_trace_id",
    "set_trace_id",
    "get_request_id",
    "set_request_id",
    "generate_trace_id",
    "generate_request_id",
    "get_structured_logger",
    "LogContext",
    "log_info",
    "log_warning",
    "log_error",
    "log_debug",
    "JSONFormatter",
    "setup_json_logging",
]
