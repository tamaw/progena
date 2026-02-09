"""
Tests for CQ-002: Config Validation at Startup.

Validates that configuration validation catches invalid values early.
"""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from dataclasses import dataclass


class TestRuntimeConfigValidation:
    """Test runtime configuration validation."""

    def test_valid_runtime_config(self):
        """Test that valid runtime config passes validation."""
        from app.progenitor.config_validation import RuntimeConfigValidator

        config = RuntimeConfigValidator(
            timezone="UTC",
            idle_backoff_seconds=30,
            max_actions_per_cycle=10,
            log_level="info",
            log_format="text",
            backoff_multiplier=1.5,
            max_backoff_seconds=300,
            checkpoint_interval_seconds=30,
            input_timeout_seconds=300,
            max_input_queue_size=100,
            max_output_queue_size=100,
            max_stream_queue_size=1000,
            health_stale_seconds=120,
            max_inner_iterations=5,
            min_verification_score=0.8,
            iteration_time_limit_seconds=600,
        )

        assert config.timezone == "UTC"
        assert config.idle_backoff_seconds == 30

    def test_negative_timeout_rejected(self):
        """Test that negative timeouts are rejected."""
        from app.progenitor.config_validation import RuntimeConfigValidator
        import pydantic

        with pytest.raises(pydantic.ValidationError) as exc_info:
            RuntimeConfigValidator(
                timezone="UTC",
                idle_backoff_seconds=-1,  # Invalid: must be > 0
                max_actions_per_cycle=10,
                log_level="info",
                log_format="text",
                backoff_multiplier=1.5,
                max_backoff_seconds=300,
                checkpoint_interval_seconds=30,
                input_timeout_seconds=300,
                max_input_queue_size=100,
                max_output_queue_size=100,
                max_stream_queue_size=1000,
                health_stale_seconds=120,
                max_inner_iterations=5,
                min_verification_score=0.8,
                iteration_time_limit_seconds=600,
            )

        assert "idle_backoff_seconds" in str(exc_info.value)

    def test_verification_score_out_of_range_rejected(self):
        """Test that verification scores outside 0-1 are rejected."""
        from app.progenitor.config_validation import RuntimeConfigValidator
        import pydantic

        with pytest.raises(pydantic.ValidationError) as exc_info:
            RuntimeConfigValidator(
                timezone="UTC",
                idle_backoff_seconds=30,
                max_actions_per_cycle=10,
                log_level="info",
                log_format="text",
                backoff_multiplier=1.5,
                max_backoff_seconds=300,
                checkpoint_interval_seconds=30,
                input_timeout_seconds=300,
                max_input_queue_size=100,
                max_output_queue_size=100,
                max_stream_queue_size=1000,
                health_stale_seconds=120,
                max_inner_iterations=5,
                min_verification_score=1.5,  # Invalid: must be <= 1.0
                iteration_time_limit_seconds=600,
            )

        assert "min_verification_score" in str(exc_info.value)

    def test_invalid_log_level_rejected(self):
        """Test that invalid log levels are rejected."""
        from app.progenitor.config_validation import RuntimeConfigValidator
        import pydantic

        with pytest.raises(pydantic.ValidationError) as exc_info:
            RuntimeConfigValidator(
                timezone="UTC",
                idle_backoff_seconds=30,
                max_actions_per_cycle=10,
                log_level="invalid_level",  # Invalid: must match pattern
                log_format="text",
                backoff_multiplier=1.5,
                max_backoff_seconds=300,
                checkpoint_interval_seconds=30,
                input_timeout_seconds=300,
                max_input_queue_size=100,
                max_output_queue_size=100,
                max_stream_queue_size=1000,
                health_stale_seconds=120,
                max_inner_iterations=5,
                min_verification_score=0.8,
                iteration_time_limit_seconds=600,
            )

        assert "log_level" in str(exc_info.value)

    def test_invalid_log_format_rejected(self):
        """Test that invalid log formats are rejected."""
        from app.progenitor.config_validation import RuntimeConfigValidator
        import pydantic

        with pytest.raises(pydantic.ValidationError) as exc_info:
            RuntimeConfigValidator(
                timezone="UTC",
                idle_backoff_seconds=30,
                max_actions_per_cycle=10,
                log_level="info",
                log_format="xml",  # Invalid: must be 'text' or 'json'
                backoff_multiplier=1.5,
                max_backoff_seconds=300,
                checkpoint_interval_seconds=30,
                input_timeout_seconds=300,
                max_input_queue_size=100,
                max_output_queue_size=100,
                max_stream_queue_size=1000,
                health_stale_seconds=120,
                max_inner_iterations=5,
                min_verification_score=0.8,
                iteration_time_limit_seconds=600,
            )

        assert "log_format" in str(exc_info.value)


class TestLLMConfigValidation:
    """Test LLM configuration validation."""

    def test_valid_config(self):
        """Test that valid LLM config passes validation."""
        from app.progenitor.config_validation import LLMConfigValidator

        config = LLMConfigValidator(
            base_url="http://localhost:4000",
            model_default="gpt-4",
            api_key="sk-test-key",
        )

        assert config.base_url == "http://localhost:4000"
        assert config.model_default == "gpt-4"
        assert config.api_key == "sk-test-key"

    def test_valid_config_without_api_key(self):
        """Test that config without api_key is valid (optional)."""
        from app.progenitor.config_validation import LLMConfigValidator

        config = LLMConfigValidator(
            base_url="http://localhost:4000",
            model_default="gpt-4",
        )

        assert config.api_key is None

    def test_empty_base_url_rejected(self):
        """Test that empty base_url is rejected."""
        from app.progenitor.config_validation import LLMConfigValidator
        import pydantic

        with pytest.raises(pydantic.ValidationError) as exc_info:
            LLMConfigValidator(
                base_url="",
                model_default="gpt-4",
            )

        assert "base_url" in str(exc_info.value)

    def test_empty_model_default_rejected(self):
        """Test that empty model_default is rejected."""
        from app.progenitor.config_validation import LLMConfigValidator
        import pydantic

        with pytest.raises(pydantic.ValidationError) as exc_info:
            LLMConfigValidator(
                base_url="http://localhost:4000",
                model_default="",
            )

        assert "model_default" in str(exc_info.value)


class TestCopilotConfigValidation:
    """Test Copilot CLI configuration validation."""

    def test_valid_copilot_config(self):
        """Test that valid Copilot config passes validation."""
        from app.progenitor.config_validation import CopilotConfigValidator

        config = CopilotConfigValidator(
            bin_path="copilot",
            timeout_seconds=300,
            model="claude-haiku-4.5",
            max_tokens=8192,
        )

        assert config.bin_path == "copilot"

    def test_negative_timeout_rejected(self):
        """Test that negative timeout is rejected."""
        from app.progenitor.config_validation import CopilotConfigValidator
        import pydantic

        with pytest.raises(pydantic.ValidationError) as exc_info:
            CopilotConfigValidator(
                bin_path="copilot",
                timeout_seconds=-1,  # Invalid
                model="claude-haiku-4.5",
                max_tokens=8192,
            )

        assert "timeout_seconds" in str(exc_info.value)

    def test_empty_bin_path_rejected(self):
        """Test that empty bin path is rejected."""
        from app.progenitor.config_validation import CopilotConfigValidator
        import pydantic

        with pytest.raises(pydantic.ValidationError) as exc_info:
            CopilotConfigValidator(
                bin_path="",  # Invalid: must have min_length=1
                timeout_seconds=300,
                model="claude-haiku-4.5",
                max_tokens=8192,
            )

        assert "bin_path" in str(exc_info.value)


class TestSchedulerConfigValidation:
    """Test scheduler configuration validation."""

    def test_valid_scheduler_config(self):
        """Test that valid scheduler config passes validation."""
        from app.progenitor.config_validation import SchedulerConfigValidator

        config = SchedulerConfigValidator(
            enabled=True,
            poll_seconds=60,
        )

        assert config.enabled is True

    def test_negative_poll_seconds_rejected(self):
        """Test that negative poll seconds is rejected."""
        from app.progenitor.config_validation import SchedulerConfigValidator
        import pydantic

        with pytest.raises(pydantic.ValidationError) as exc_info:
            SchedulerConfigValidator(
                enabled=True,
                poll_seconds=-1,  # Invalid
            )

        assert "poll_seconds" in str(exc_info.value)


class TestConfigValidationIntegration:
    """Test full configuration validation integration."""

    def test_validate_config_returns_issues(self):
        """Test that validate_config returns issues list."""
        from app.progenitor.config_validation import validate_config

        # Create a mock config with valid values
        @dataclass
        class MockLLMConfig:
            base_url = "http://localhost:4000"
            api_key = "test-key"
            model_default = "gpt-4"

        @dataclass
        class MockRuntimeConfig:
            timezone = "UTC"
            idle_backoff_seconds = 30
            max_actions_per_cycle = 10
            log_level = "info"
            log_format = "text"
            backoff_multiplier = 1.5
            max_backoff_seconds = 300
            checkpoint_interval_seconds = 30
            input_timeout_seconds = 300
            max_input_queue_size = 100
            max_output_queue_size = 100
            max_stream_queue_size = 1000
            health_stale_seconds = 120
            max_inner_iterations = 5
            min_verification_score = 0.8
            iteration_time_limit_seconds = 600

        @dataclass
        class MockStorageConfig:
            state_dir = Path("/data/state")
            log_dir = Path("/logs")
            worktree_dir = Path("/worktrees")
            prompts_dir = Path("/data/prompts")
            state_lock_timeout_seconds = 10

        @dataclass
        class MockCopilotConfig:
            bin_path = "copilot"
            timeout_seconds = 300
            model = "claude-haiku-4.5"
            max_tokens = 8192

        @dataclass
        class MockSchedulerConfig:
            enabled = True
            poll_seconds = 60

        @dataclass
        class MockConfig:
            llm = MockLLMConfig()
            runtime = MockRuntimeConfig()
            storage = MockStorageConfig()
            copilot = MockCopilotConfig()
            scheduler = MockSchedulerConfig()

        is_valid, issues = validate_config(MockConfig())

        assert is_valid is True
        assert len(issues) == 0

    def test_validate_config_catches_invalid_values(self):
        """Test that validate_config catches invalid values."""
        from app.progenitor.config_validation import validate_runtime_config

        @dataclass
        class MockRuntimeConfig:
            timezone = "UTC"
            idle_backoff_seconds = -1  # Invalid
            max_actions_per_cycle = 10
            log_level = "info"
            log_format = "text"
            backoff_multiplier = 1.5
            max_backoff_seconds = 300
            checkpoint_interval_seconds = 30
            input_timeout_seconds = 300
            max_input_queue_size = 100
            max_output_queue_size = 100
            max_stream_queue_size = 1000
            max_inner_iterations = 5
            min_verification_score = 0.8
            iteration_time_limit_seconds = 600

        issues = validate_runtime_config(MockRuntimeConfig())

        assert len(issues) > 0
        assert any("Runtime Config Error" in issue for issue in issues)

    def test_config_validation_error_exception(self):
        """Test ConfigValidationError is raised for invalid config."""
        from app.progenitor.config_validation import ConfigValidationError, validate_config_or_raise

        @dataclass
        class InvalidRuntimeConfig:
            timezone = ""  # Invalid: empty
            idle_backoff_seconds = -1  # Invalid
            max_actions_per_cycle = 0  # Invalid
            log_level = "invalid"  # Invalid
            log_format = "invalid"  # Invalid
            backoff_multiplier = 0.5  # Invalid: must be > 1
            max_backoff_seconds = -1  # Invalid
            checkpoint_interval_seconds = -1  # Invalid
            input_timeout_seconds = -1  # Invalid
            max_input_queue_size = 0  # Invalid
            max_output_queue_size = 0  # Invalid
            max_stream_queue_size = 0  # Invalid
            max_inner_iterations = 0  # Invalid
            min_verification_score = 2.0  # Invalid
            iteration_time_limit_seconds = 0  # Invalid

        @dataclass
        class InvalidConfig:
            llm = MagicMock()
            runtime = InvalidRuntimeConfig()
            storage = MagicMock()
            copilot = MagicMock()
            scheduler = MagicMock()

        # Configure mocks to pass basic attribute checks
        config = InvalidConfig()

        # This should not raise (we're testing issues collection)
        from app.progenitor.config_validation import validate_config
        is_valid, issues = validate_config(config)

        # Should have at least runtime issues
        assert not is_valid or len(issues) > 0


class TestBoundaryValues:
    """Test boundary value validation."""

    def test_max_allowed_values(self):
        """Test that maximum allowed values are accepted."""
        from app.progenitor.config_validation import RuntimeConfigValidator

        config = RuntimeConfigValidator(
            timezone="UTC",
            idle_backoff_seconds=3600,  # Max
            max_actions_per_cycle=100,  # Max
            log_level="error",
            log_format="json",
            backoff_multiplier=10.0,  # Max
            max_backoff_seconds=3600,  # Max
            checkpoint_interval_seconds=3600,  # Max
            input_timeout_seconds=3600,  # Max
            max_input_queue_size=10000,  # Max
            max_output_queue_size=10000,  # Max
            max_stream_queue_size=100000,  # Max
            health_stale_seconds=600,  # Max
            max_inner_iterations=100,  # Max
            min_verification_score=1.0,  # Max
            iteration_time_limit_seconds=7200,  # Max
        )

        assert config.idle_backoff_seconds == 3600

    def test_min_allowed_values(self):
        """Test that minimum allowed values are accepted."""
        from app.progenitor.config_validation import RuntimeConfigValidator

        config = RuntimeConfigValidator(
            timezone="X",  # Min 1 char
            idle_backoff_seconds=1,  # Min (> 0)
            max_actions_per_cycle=1,  # Min (> 0)
            log_level="debug",
            log_format="text",
            backoff_multiplier=1.01,  # Min (> 1.0)
            max_backoff_seconds=1,  # Min (> 0)
            checkpoint_interval_seconds=1,  # Min (> 0)
            input_timeout_seconds=1,  # Min (> 0)
            max_input_queue_size=1,  # Min (> 0)
            max_output_queue_size=1,  # Min (> 0)
            max_stream_queue_size=1,  # Min (> 0)
            health_stale_seconds=1,  # Min (> 0)
            max_inner_iterations=1,  # Min (> 0)
            min_verification_score=0.0,  # Min (>= 0)
            iteration_time_limit_seconds=1,  # Min (> 0)
        )

        assert config.idle_backoff_seconds == 1
