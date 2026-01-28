"""
Langfuse Client Module.

Provides Langfuse-wrapped OpenAI clients for automatic observability.
Uses the Langfuse drop-in replacement pattern for minimal code changes.
"""

import atexit
import logging
from functools import wraps
from typing import Any, Callable, Optional, TypeVar

from .config import get_langfuse_config

logger = logging.getLogger(__name__)

# Type variable for decorator
F = TypeVar("F", bound=Callable[..., Any])

# Langfuse client singleton
_langfuse_client = None
_initialized = False


def _init_langfuse() -> bool:
    """Initialize Langfuse client if configured.

    Returns:
        True if Langfuse was successfully initialized, False otherwise.
    """
    global _langfuse_client, _initialized

    if _initialized:
        return _langfuse_client is not None

    config = get_langfuse_config()

    if not config.is_configured():
        logger.debug("Langfuse not configured, skipping initialization")
        _initialized = True
        return False

    try:
        from langfuse import Langfuse  # type: ignore[import-untyped]

        _langfuse_client = Langfuse(
            public_key=config.public_key,
            secret_key=config.secret_key,
            host=config.host,
            debug=config.debug,
            sample_rate=config.sample_rate,
        )

        # Register flush on exit
        atexit.register(_flush_on_exit)

        logger.info("Langfuse client initialized successfully")
        _initialized = True
        return True

    except ImportError:
        logger.warning(
            "langfuse package not installed. Run: pip install langfuse"
        )
        _initialized = True
        return False
    except Exception as e:
        logger.error("Failed to initialize Langfuse client: %s", e)
        _initialized = True
        return False


def _flush_on_exit() -> None:
    """Flush Langfuse on program exit."""
    if _langfuse_client is not None:
        try:
            _langfuse_client.flush()
        except Exception as e:
            logger.debug("Error flushing Langfuse on exit: %s", e)


def get_langfuse_client():
    """Get the Langfuse client instance.

    Returns:
        Langfuse client or None if not configured.
    """
    _init_langfuse()
    return _langfuse_client


def get_openai_client(async_client: bool = True):
    """Get an OpenAI client wrapped with Langfuse observability.

    If Langfuse is not configured, returns a standard OpenAI client.

    Args:
        async_client: If True, return AsyncOpenAI, else OpenAI.

    Returns:
        OpenAI or AsyncOpenAI client (Langfuse-wrapped if configured).
    """
    config = get_langfuse_config()

    if config.is_configured():
        try:
            if async_client:
                from langfuse.openai import AsyncOpenAI  # type: ignore[import-untyped]
                return AsyncOpenAI()
            else:
                from langfuse.openai import OpenAI  # type: ignore[import-untyped]
                return OpenAI()
        except ImportError:
            logger.warning(
                "langfuse package not installed, using standard OpenAI client"
            )

    # Fallback to standard OpenAI client
    if async_client:
        from openai import AsyncOpenAI
        return AsyncOpenAI()
    else:
        from openai import OpenAI
        return OpenAI()


def get_azure_openai_client(
    azure_endpoint: str,
    api_key: str,
    api_version: str = "2024-02-15-preview",
    async_client: bool = True,
):
    """Get an Azure OpenAI client wrapped with Langfuse observability.

    If Langfuse is not configured, returns a standard Azure OpenAI client.

    Args:
        azure_endpoint: Azure OpenAI endpoint URL.
        api_key: Azure OpenAI API key.
        api_version: Azure API version.
        async_client: If True, return AsyncAzureOpenAI, else AzureOpenAI.

    Returns:
        AzureOpenAI or AsyncAzureOpenAI client (Langfuse-wrapped if configured).
    """
    config = get_langfuse_config()

    if config.is_configured():
        try:
            if async_client:
                from langfuse.openai import AsyncAzureOpenAI  # type: ignore[import-untyped]
                return AsyncAzureOpenAI(
                    azure_endpoint=azure_endpoint,
                    api_key=api_key,
                    api_version=api_version,
                )
            else:
                from langfuse.openai import AzureOpenAI  # type: ignore[import-untyped]
                return AzureOpenAI(
                    azure_endpoint=azure_endpoint,
                    api_key=api_key,
                    api_version=api_version,
                )
        except ImportError:
            logger.warning(
                "langfuse package not installed, using standard Azure OpenAI client"
            )

    # Fallback to standard Azure OpenAI client
    if async_client:
        from openai import AsyncAzureOpenAI
        return AsyncAzureOpenAI(
            azure_endpoint=azure_endpoint,
            api_key=api_key,
            api_version=api_version,
        )
    else:
        from openai import AzureOpenAI
        return AzureOpenAI(
            azure_endpoint=azure_endpoint,
            api_key=api_key,
            api_version=api_version,
        )


def flush_langfuse() -> None:
    """Flush all pending Langfuse events.

    Call this before the application exits or at key checkpoints
    to ensure all traces are sent to Langfuse.
    """
    if _langfuse_client is not None:
        try:
            _langfuse_client.flush()
            logger.debug("Langfuse events flushed")
        except Exception as e:
            logger.warning("Error flushing Langfuse: %s", e)


def shutdown_langfuse() -> None:
    """Shutdown the Langfuse client.

    Flushes pending events and releases resources.
    """
    global _langfuse_client, _initialized

    if _langfuse_client is not None:
        try:
            _langfuse_client.flush()
            _langfuse_client.shutdown()
            logger.info("Langfuse client shutdown complete")
        except Exception as e:
            logger.warning("Error shutting down Langfuse: %s", e)
        finally:
            _langfuse_client = None
            _initialized = False


def observe(
    name: Optional[str] = None,
    session_id: Optional[str] = None,
    user_id: Optional[str] = None,
    tags: Optional[list[str]] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> Callable[[F], F]:
    """Decorator for observing functions with Langfuse.

    If Langfuse is not configured, this decorator is a no-op.

    Args:
        name: Name for the trace (defaults to function name).
        session_id: Session identifier for grouping traces.
        user_id: User identifier.
        tags: Tags for filtering traces.
        metadata: Additional metadata.

    Returns:
        Decorated function with Langfuse tracing.
    """
    config = get_langfuse_config()

    def decorator(func: F) -> F:
        if not config.is_configured():
            return func

        try:
            from langfuse import observe as langfuse_observe  # type: ignore[import-untyped]

            # Apply Langfuse decorator
            wrapped = langfuse_observe(name=name or func.__name__)(func)

            @wraps(func)
            def wrapper(*args, **kwargs):
                # Add session/user/tags via metadata if provided
                extra_kwargs = {}
                if session_id:
                    extra_kwargs["langfuse_session_id"] = session_id
                if user_id:
                    extra_kwargs["langfuse_user_id"] = user_id
                if tags:
                    extra_kwargs["langfuse_tags"] = tags
                if metadata:
                    extra_kwargs["langfuse_metadata"] = metadata

                # Merge with existing metadata
                if "metadata" in kwargs:
                    kwargs["metadata"] = {**kwargs["metadata"], **extra_kwargs}
                elif extra_kwargs:
                    kwargs["metadata"] = extra_kwargs

                return wrapped(*args, **kwargs)

            return wrapper  # type: ignore

        except ImportError:
            return func

    return decorator
