"""
Configuration validation for Progenitor.

Provides early validation of configuration values to catch errors at startup
rather than during runtime. Uses Pydantic for strong type validation.
"""

import logging
from pathlib import Path
from typing import Optional, Any

from pydantic import BaseModel, Field, field_validator, model_validator

logger = logging.getLogger(__name__)


class ConfigValidationError(Exception):
    """Raised when configuration validation fails."""
    pass


class LLMConfigValidator(BaseModel):
    """Validates LLM configuration."""

    base_url: str = Field(min_length=1)
    api_key: Optional[str] = None
    model_default: str = Field(min_length=1)


class RuntimeConfigValidator(BaseModel):
    """Validates runtime configuration."""

    timezone: str = Field(min_length=1)
    idle_backoff_seconds: int = Field(gt=0, le=3600)
    max_actions_per_cycle: int = Field(gt=0, le=100)
    log_level: str = Field(pattern=r"^(debug|info|warn|warning|error)$")
    log_format: str = Field(pattern=r"^(text|json)$")
    backoff_multiplier: float = Field(gt=1.0, le=10.0)
    max_backoff_seconds: int = Field(gt=0, le=3600)
    checkpoint_interval_seconds: int = Field(gt=0, le=3600)
    input_timeout_seconds: int = Field(gt=0, le=3600)

    # Queue bounds
    max_input_queue_size: int = Field(gt=0, le=10000)
    max_output_queue_size: int = Field(gt=0, le=10000)
    max_stream_queue_size: int = Field(gt=0, le=100000)

    # Health check settings
    health_stale_seconds: int = Field(gt=0, le=600)

    # Inner loop settings
    max_inner_iterations: int = Field(gt=0, le=100)
    min_verification_score: float = Field(ge=0.0, le=1.0)
    iteration_time_limit_seconds: int = Field(gt=0, le=7200)


class StorageConfigValidator(BaseModel):
    """Validates storage configuration."""

    state_dir: Path
    log_dir: Path
    worktree_dir: Path
    prompts_dir: Path
    state_lock_timeout_seconds: int = Field(gt=0, le=300)

    @field_validator('state_dir', 'log_dir', 'worktree_dir', 'prompts_dir', mode='after')
    @classmethod
    def validate_path_is_absolute_or_relative(cls, v: Path) -> Path:
        """Ensure paths are valid."""
        # Convert to Path if string
        if isinstance(v, str):
            v = Path(v)
        return v


class CopilotConfigValidator(BaseModel):
    """Validates Copilot CLI configuration."""

    bin_path: str = Field(min_length=1)
    timeout_seconds: int = Field(gt=0, le=1800)
    model: str = Field(min_length=1)
    max_tokens: int = Field(gt=0, le=1000000)


class SchedulerConfigValidator(BaseModel):
    """Validates scheduler configuration."""

    enabled: bool = True
    poll_seconds: int = Field(gt=0, le=3600)


def validate_llm_config(config) -> list[str]:
    """Validate LLM configuration and return list of warnings/errors."""
    issues = []

    try:
        LLMConfigValidator(
            base_url=config.base_url,
            api_key=config.api_key,
            model_default=config.model_default,
        )
    except Exception as e:
        issues.append(f"LLM Config Error: {e}")

    return issues


def validate_runtime_config(config) -> list[str]:
    """Validate runtime configuration and return list of warnings/errors."""
    issues = []

    try:
        RuntimeConfigValidator(
            timezone=config.timezone,
            idle_backoff_seconds=config.idle_backoff_seconds,
            max_actions_per_cycle=config.max_actions_per_cycle,
            log_level=config.log_level,
            log_format=config.log_format,
            backoff_multiplier=config.backoff_multiplier,
            max_backoff_seconds=config.max_backoff_seconds,
            checkpoint_interval_seconds=config.checkpoint_interval_seconds,
            input_timeout_seconds=config.input_timeout_seconds,
            max_input_queue_size=config.max_input_queue_size,
            max_output_queue_size=config.max_output_queue_size,
            max_stream_queue_size=config.max_stream_queue_size,
            health_stale_seconds=config.health_stale_seconds,
            max_inner_iterations=config.max_inner_iterations,
            min_verification_score=config.min_verification_score,
            iteration_time_limit_seconds=config.iteration_time_limit_seconds,
        )
    except Exception as e:
        issues.append(f"Runtime Config Error: {e}")

    return issues


def validate_storage_config(config) -> list[str]:
    """Validate storage configuration and return list of warnings/errors."""
    issues = []

    try:
        StorageConfigValidator(
            state_dir=config.state_dir,
            log_dir=config.log_dir,
            worktree_dir=config.worktree_dir,
            prompts_dir=config.prompts_dir,
            state_lock_timeout_seconds=config.state_lock_timeout_seconds,
        )
    except Exception as e:
        issues.append(f"Storage Config Error: {e}")

    return issues


def validate_copilot_config(config) -> list[str]:
    """Validate Copilot CLI configuration and return list of warnings/errors."""
    issues = []

    try:
        CopilotConfigValidator(
            bin_path=config.bin_path,
            timeout_seconds=config.timeout_seconds,
            model=config.model,
            max_tokens=config.max_tokens,
        )
    except Exception as e:
        issues.append(f"Copilot Config Error: {e}")

    return issues


def validate_scheduler_config(config) -> list[str]:
    """Validate scheduler configuration and return list of warnings/errors."""
    issues = []

    try:
        SchedulerConfigValidator(
            enabled=config.enabled,
            poll_seconds=config.poll_seconds,
        )
    except Exception as e:
        issues.append(f"Scheduler Config Error: {e}")

    return issues


def validate_config(config) -> tuple[bool, list[str]]:
    """
    Validate the entire Progenitor configuration.

    Returns:
        Tuple of (is_valid: bool, issues: list[str])
    """
    issues = []

    # Validate each section
    issues.extend(validate_llm_config(config.llm))
    issues.extend(validate_runtime_config(config.runtime))
    issues.extend(validate_storage_config(config.storage))
    issues.extend(validate_copilot_config(config.copilot))
    issues.extend(validate_scheduler_config(config.scheduler))

    is_valid = len(issues) == 0

    if issues:
        for issue in issues:
            logger.error("Configuration validation failed: %s", issue)
    else:
        logger.info("Configuration validation passed")

    return is_valid, issues


def validate_config_or_raise(config) -> None:
    """
    Validate the configuration and raise ConfigValidationError if invalid.

    This should be called at startup to fail fast on invalid configuration.
    """
    is_valid, issues = validate_config(config)

    if not is_valid:
        raise ConfigValidationError(
            f"Configuration validation failed with {len(issues)} error(s):\n" +
            "\n".join(f"  - {issue}" for issue in issues)
        )
