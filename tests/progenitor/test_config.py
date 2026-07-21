"""
Tests for the centralized configuration module.
"""

import json
import logging
import os
from pathlib import Path
from unittest.mock import patch

from app.progenitor.config import (
    LLMConfig,
    MCPConfig,
    MCPServerConfig,
    ProgenitorConfig,
    RuntimeConfig,
    SchedulerConfig,
    StorageConfig,
    get_config,
    reload_config,
    resolve_schedule_config_path,
)
from app.progenitor.mcp.loader import load_mcp_servers, resolve_required_env_template
from app.progenitor.prompts import DEFAULT_PROMPTS_DIR
from app.progenitor.runtime.logging import setup_logging
from app.progenitor.utils import env_bool, env_float, env_int, env_list


class TestEnvParsers:
    """Tests for environment variable parsing helpers."""

    def testenv_bool_true_values(self):
        """Test parsing boolean true values."""
        with patch.dict(os.environ, {"TEST_BOOL": "true"}):
            assert env_bool("TEST_BOOL") is True
        with patch.dict(os.environ, {"TEST_BOOL": "1"}):
            assert env_bool("TEST_BOOL") is True
        with patch.dict(os.environ, {"TEST_BOOL": "yes"}):
            assert env_bool("TEST_BOOL") is True
        with patch.dict(os.environ, {"TEST_BOOL": "on"}):
            assert env_bool("TEST_BOOL") is True

    def testenv_bool_false_values(self):
        """Test parsing boolean false values."""
        with patch.dict(os.environ, {"TEST_BOOL": "false"}):
            assert env_bool("TEST_BOOL") is False
        with patch.dict(os.environ, {"TEST_BOOL": "0"}):
            assert env_bool("TEST_BOOL") is False
        with patch.dict(os.environ, {"TEST_BOOL": "no"}):
            assert env_bool("TEST_BOOL") is False

    def testenv_bool_default(self):
        """Test boolean default when not set."""
        with patch.dict(os.environ, {}, clear=True):
            # Remove the key if it exists
            os.environ.pop("TEST_BOOL_MISSING", None)
            assert env_bool("TEST_BOOL_MISSING", default=True) is True
            assert env_bool("TEST_BOOL_MISSING", default=False) is False

    def testenv_int_valid(self):
        """Test parsing valid integers."""
        with patch.dict(os.environ, {"TEST_INT": "42"}):
            assert env_int("TEST_INT", 0) == 42

    def testenv_int_invalid_returns_default(self):
        """Test invalid integer returns default."""
        with patch.dict(os.environ, {"TEST_INT": "not_a_number"}):
            assert env_int("TEST_INT", 100) == 100

    def testenv_int_missing_returns_default(self):
        """Test missing env var returns default."""
        os.environ.pop("TEST_INT_MISSING", None)
        assert env_int("TEST_INT_MISSING", 50) == 50

    def testenv_float_valid(self):
        """Test parsing valid floats."""
        with patch.dict(os.environ, {"TEST_FLOAT": "3.14"}):
            assert env_float("TEST_FLOAT", 0.0) == 3.14

    def testenv_list_valid(self):
        """Test parsing comma-separated list."""
        with patch.dict(os.environ, {"TEST_LIST": "a,b, c , d"}):
            result = env_list("TEST_LIST")
            assert result == ["a", "b", "c", "d"]

    def testenv_list_empty(self):
        """Test empty list when not set."""
        os.environ.pop("TEST_LIST_MISSING", None)
        assert env_list("TEST_LIST_MISSING") == []


class TestLLMConfig:
    """Tests for LLM configuration."""

    def test_from_env(self):
        """Test loading LLM config from environment."""
        env = {
            "AZURE_OPENAI_BASE_URL": "https://example.openai.azure.com/openai/v1",
            "AZURE_OPENAI_API_KEY": "sk-test-key",
            "PROGENITOR_MODEL_DEFAULT": "gpt-4o",
            # Set explicitly so the test is hermetic: a local .env sets this to
            # "medium" but CI leaves it unset (code default "low"). Pinning the
            # env value keeps the assertion stable in both.
            "PROGENITOR_REASONING_EFFORT": "medium",
        }
        with patch.dict(os.environ, env, clear=False):
            config = LLMConfig.from_env()
            assert config.base_url == "https://example.openai.azure.com/openai/v1"
            assert config.api_key == "sk-test-key"
            assert config.model_default == "gpt-4o"
            assert config.reasoning_enabled is True
            assert config.reasoning_effort == "medium"
            assert config.reasoning_summary == "detailed"

    def test_defaults(self):
        """Test default values."""
        config = LLMConfig()
        assert config.base_url == ""
        assert config.model_default == "gpt-chat-latest"
        assert config.reasoning_enabled is True
        assert config.reasoning_effort == "low"
        assert config.reasoning_summary == "detailed"

    def test_reasoning_effort_from_env(self):
        """Test loading reasoning effort from environment."""
        with patch.dict(
            os.environ,
            {"PROGENITOR_REASONING_EFFORT": "medium"},
            clear=False,
        ):
            config = LLMConfig.from_env()
            assert config.reasoning_effort == "medium"

    def test_reasoning_summary_from_env(self):
        """Test loading reasoning summary mode from environment."""
        with patch.dict(
            os.environ,
            {"PROGENITOR_REASONING_SUMMARY": "detailed"},
            clear=False,
        ):
            config = LLMConfig.from_env()
            assert config.reasoning_summary == "detailed"


class TestRuntimeConfig:
    """Tests for runtime configuration."""

    def test_defaults(self):
        """Test default values match spec."""
        config = RuntimeConfig()
        assert config.timezone == "Europe/London"
        assert config.idle_backoff_seconds == 30
        assert config.mcp_refresh_seconds == 900
        assert config.registry_refresh_seconds == 900
        assert config.max_actions_per_cycle == 10
        assert config.log_level == "info"
        assert config.http_host == "127.0.0.1"
        assert config.http_port == 9501

    def test_from_env_overrides_defaults(self):
        """Test environment variables override defaults."""
        env = {
            "PROGENITOR_TIMEZONE": "UTC",
            "PROGENITOR_IDLE_BACKOFF_SECONDS": "60",
            "PROGENITOR_MCP_REFRESH_SECONDS": "900",
            "PROGENITOR_REGISTRY_REFRESH_SECONDS": "1200",
            "PROGENITOR_MAX_ACTIONS_PER_CYCLE": "20",
            "PROGENITOR_LOG_LEVEL": "debug",
            "PROGENITOR_HTTP_HOST": "0.0.0.0",
            "PROGENITOR_HTTP_PORT": "9901",
            "PROGENITOR_HTTP_PUBLIC_BASE_URL": "http://example.test/progenitor",
        }
        with patch.dict(os.environ, env, clear=False):
            config = RuntimeConfig.from_env()
            assert config.timezone == "UTC"
            assert config.idle_backoff_seconds == 60
            assert config.mcp_refresh_seconds == 900
            assert config.registry_refresh_seconds == 1200
            assert config.max_actions_per_cycle == 20
            assert config.log_level == "debug"
            assert config.http_host == "0.0.0.0"
            assert config.http_port == 9901
            assert config.http_public_base_url == "http://example.test/progenitor"

    def test_from_env_uses_code_defaults_without_file_fallback(self):
        """Runtime defaults come from code/env, not saved config payloads."""
        env_clear = {
            "PROGENITOR_TIMEZONE": "",
            "PROGENITOR_IDLE_BACKOFF_SECONDS": "",
            "PROGENITOR_MCP_REFRESH_SECONDS": "",
            "PROGENITOR_REGISTRY_REFRESH_SECONDS": "",
        }
        with patch.dict(os.environ, env_clear, clear=False):
            os.environ.pop("PROGENITOR_TIMEZONE", None)
            os.environ.pop("PROGENITOR_IDLE_BACKOFF_SECONDS", None)
            os.environ.pop("PROGENITOR_MCP_REFRESH_SECONDS", None)
            os.environ.pop("PROGENITOR_REGISTRY_REFRESH_SECONDS", None)
            config = RuntimeConfig.from_env()
            assert config.timezone == "Europe/London"
            assert config.idle_backoff_seconds == 30
            assert config.mcp_refresh_seconds == 900
            assert config.registry_refresh_seconds == 900


class TestStorageConfig:
    """Tests for storage configuration."""

    def test_defaults_with_project_root(self):
        """Test defaults are project-relative when project_root is provided."""
        project_root = Path("/test/project")
        env = {
            "PROGENITOR_DATA_DIR": "",
            "PROGENITOR_STATE_DIR": "",
            "PROGENITOR_LOG_DIR": "",
            "PROGENITOR_WORKTREE_DIR": "",
            "PROGENITOR_WORKTREES_ROOT": "",
            "PROGENITOR_PROMPTS_DIR": "",
        }
        with patch.dict(os.environ, env, clear=False):
            for key in env:
                os.environ.pop(key, None)
            config = StorageConfig.from_env(project_root)
        assert config.data_dir == project_root / "data"
        assert config.state_dir == project_root / "data" / "state"
        assert config.log_dir == project_root / "logs"
        assert config.worktree_dir == project_root / "worktrees"
        assert config.prompts_dir == project_root / "data" / "prompts"

    def test_default_prompts_dir_uses_checked_in_repo_path(self):
        """Storage defaults should use the checked-in repo paths when env is unset."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PROGENITOR_DATA_DIR", None)
            os.environ.pop("PROGENITOR_STATE_DIR", None)
            os.environ.pop("PROGENITOR_LOG_DIR", None)
            os.environ.pop("PROGENITOR_WORKTREE_DIR", None)
            os.environ.pop("PROGENITOR_WORKTREES_ROOT", None)
            os.environ.pop("PROGENITOR_PROMPTS_DIR", None)
            config = StorageConfig.from_env()
        repo_root = Path(__file__).resolve().parents[2]
        assert config.data_dir == repo_root / "data"
        assert config.state_dir == repo_root / "data" / "state"
        assert config.log_dir == repo_root / "logs"
        assert config.worktree_dir == repo_root / "worktrees"
        assert config.prompts_dir == DEFAULT_PROMPTS_DIR

    def test_env_overrides(self):
        """Test environment variables override defaults."""
        env = {
            "PROGENITOR_DATA_DIR": "/custom/data",
            "PROGENITOR_LOG_DIR": "/custom/logs",
            "PROGENITOR_WORKTREE_DIR": "/custom/worktrees",
            "PROGENITOR_STATE_LOCK_TIMEOUT_SECONDS": "30",
        }
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop("PROGENITOR_STATE_DIR", None)
            os.environ.pop("PROGENITOR_PROMPTS_DIR", None)
            config = StorageConfig.from_env()
            assert config.data_dir == Path("/custom/data")
            assert config.state_dir == Path("/custom/data/state")
            assert config.log_dir == Path("/custom/logs")
            assert config.worktree_dir == Path("/custom/worktrees")
            assert config.prompts_dir == Path("/custom/data/prompts")
            assert config.state_lock_timeout_seconds == 30


class TestMCPConfig:
    """Tests for Progenitor's persisted MCP registry loader."""

    def test_resolve_schedule_config_path_uses_checked_in_default(self):
        repo_root = Path(__file__).resolve().parents[2]
        with patch.dict(os.environ, {}, clear=False):
            path = resolve_schedule_config_path()
        assert path == repo_root / "data" / "config" / "schedule.json"

    def test_load_mcp_servers_reads_mcp_servers_map(self):
        payload = json.loads(
            """{
  "mcpServers": {
    "jina": {
      "enabled": true,
      "transport": "http",
      "description": "Web search",
      "url": "https://mcp.jina.ai/v1?include_tags=search,read",
      "headers": {
        "Authorization": "Bearer ${JINA_API_KEY}"
      }
    }
  }
}"""
        )
        with patch.dict(os.environ, {"JINA_API_KEY": "jina_test_key"}, clear=False):
            servers = load_mcp_servers(payload)

        assert servers == [
            MCPServerConfig(
                name="jina",
                enabled=True,
                transport="http",
                description="Web search",
                url="https://mcp.jina.ai/v1?include_tags=search,read",
                headers={"Authorization": "Bearer jina_test_key"},
            )
        ]

    def test_load_mcp_servers_drops_headers_with_missing_env(self):
        payload = json.loads(
            """{
  "mcpServers": {
    "jina": {
      "enabled": true,
      "transport": "http",
      "url": "https://mcp.jina.ai/v1",
      "headers": {
        "Authorization": "Bearer ${JINA_API_KEY}"
      }
    }
  }
}"""
        )
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("JINA_API_KEY", None)
            servers = load_mcp_servers(payload)

        assert len(servers) == 1
        assert servers[0].name == "jina"
        assert servers[0].headers == {}

    def test_resolve_required_env_template_supports_dollar_var(self):
        with patch.dict(os.environ, {"JINA_API_KEY": "jina_test_key"}, clear=False):
            assert resolve_required_env_template("Bearer $JINA_API_KEY") == (
                "Bearer jina_test_key",
                [],
            )

    def test_resolve_required_env_template_preserves_invalid_placeholder(self):
        assert resolve_required_env_template("Bearer ${1INVALID}") == (
            "Bearer ${1INVALID}",
            [],
        )

    def test_empty_mcp_config_has_no_servers(self):
        assert load_mcp_servers({}) == []

    def test_load_reads_persisted_payload(self):
        payload = json.loads(
            """{
  "mcpServers": {
    "jina": {
      "enabled": true,
      "transport": "http",
      "url": "https://mcp.jina.ai/v1"
    }
  }
}"""
        )

        config = MCPConfig.from_payload(payload)

        assert [server.name for server in config.servers] == ["jina"]


class TestSchedulerConfig:
    """Tests for scheduler configuration."""

    def test_defaults(self):
        """Test default values."""
        config = SchedulerConfig()
        assert config.enabled is True
        assert config.poll_seconds == 30

    def test_from_env(self):
        """Test loading from environment."""
        env = {
            "PROGENITOR_SCHEDULER_ENABLED": "false",
            "PROGENITOR_SCHEDULER_POLL_SECONDS": "60",
        }
        with patch.dict(os.environ, env, clear=False):
            config = SchedulerConfig.from_env()
            assert config.enabled is False
            assert config.poll_seconds == 60


class TestProgenitorConfig:
    """Tests for full Progenitor configuration."""

    def test_load_from_env(self):
        """Test loading config from environment variables."""
        env = {
            "AZURE_OPENAI_BASE_URL": "https://example.openai.azure.com/openai/v1",
            "PROGENITOR_TIMEZONE": "Asia/Tokyo",
            "PROGENITOR_IDLE_BACKOFF_SECONDS": "15",
        }

        with patch.dict(os.environ, env, clear=False):
            config = ProgenitorConfig.load(Path("."), Path("."))

            assert config.runtime.timezone == "Asia/Tokyo"
            assert config.runtime.idle_backoff_seconds == 15

    def test_to_dict_redacts_secrets(self):
        """Test secrets are redacted in dict output."""
        config = ProgenitorConfig(
            llm=LLMConfig(
                api_key="sk-secret-key",
            )
        )

        result = config.to_dict(redact_secrets=True)

        assert result["llm"]["api_key"] == "***REDACTED***"
        assert isinstance(result["storage"]["state_dir"], str)

    def test_to_dict_without_redaction(self):
        """Test secrets are exposed when redaction is disabled."""
        config = ProgenitorConfig(
            llm=LLMConfig(
                api_key="sk-secret-key",
            )
        )

        result = config.to_dict(redact_secrets=False)

        assert result["llm"]["api_key"] == "sk-secret-key"


class TestGlobalConfig:
    """Tests for global configuration functions."""

    def test_get_config_caches_result(self):
        """Test get_config returns cached instance."""
        # Reset global config
        import app.progenitor.config as config_module

        config_module._config = None

        with patch.dict(
            os.environ,
            {"AZURE_OPENAI_BASE_URL": "https://example.openai.azure.com/openai/v1"},
            clear=False,
        ):
            config1 = get_config()
            config2 = get_config()

        assert config1 is config2

    def test_reload_config_creates_new_instance(self):
        """Test reload_config creates new instance."""
        import app.progenitor.config as config_module

        config_module._config = None

        with patch.dict(
            os.environ,
            {"AZURE_OPENAI_BASE_URL": "https://example.openai.azure.com/openai/v1"},
            clear=False,
        ):
            config1 = get_config()
            config2 = reload_config()

        assert config1 is not config2

    def test_get_config_validate_logs_non_fatal_issues(self, caplog):
        """validate=True should log warning-only config issues."""
        import app.progenitor.config as config_module

        config_module._config = None

        with (
            patch.dict(
                os.environ,
                {
                    "AZURE_OPENAI_BASE_URL": "https://example.openai.azure.com/openai/v1",
                    "PROGENITOR_LOG_FORMAT": "xml",
                },
                clear=False,
            ),
            caplog.at_level(logging.WARNING),
        ):
            get_config()

        assert "Configuration has 1 validation issue(s)" in caplog.text
        assert "invalid log_format='xml'" in caplog.text

    def test_get_config_validate_false_skips_non_fatal_issue_logging(self, caplog):
        """validate=False should skip the warning-only validation pass."""
        import app.progenitor.config as config_module

        config_module._config = None

        with (
            patch.dict(
                os.environ,
                {
                    "AZURE_OPENAI_BASE_URL": "https://example.openai.azure.com/openai/v1",
                    "PROGENITOR_LOG_FORMAT": "xml",
                },
                clear=False,
            ),
            caplog.at_level(logging.WARNING),
        ):
            get_config(validate=False)

        assert "Configuration has" not in caplog.text


class TestLoggingSetup:
    """Tests for logger setup."""

    def test_setup_logging_splits_stdout_and_stderr_files(self, tmp_path):
        original_handlers = logging.root.handlers[:]
        original_level = logging.root.level
        logger = logging.getLogger("tests.progenitor.logging")

        config = ProgenitorConfig(
            storage=StorageConfig(
                data_dir=tmp_path / "data",
                state_dir=tmp_path / "state",
                log_dir=tmp_path,
                worktree_dir=tmp_path / "worktrees",
                prompts_dir=tmp_path / "prompts",
            )
        )

        try:
            setup_logging(config, "progenitor")
            logger.info("info line")
            logger.error("error line")

            for handler in logging.root.handlers:
                handler.flush()

            stdout_log = (tmp_path / "progenitor" / "progenitor.stdout.log").read_text(
                encoding="utf-8"
            )
            stderr_log = (tmp_path / "progenitor" / "progenitor.stderr.log").read_text(
                encoding="utf-8"
            )

            assert "info line" in stdout_log
            assert "error line" not in stdout_log
            assert "error line" in stderr_log
        finally:
            for handler in logging.root.handlers[:]:
                handler.close()
                logging.root.removeHandler(handler)
            logging.root.handlers = original_handlers
            logging.root.setLevel(original_level)
