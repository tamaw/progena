"""
Tests for the centralized configuration module.
"""

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
    load_mcp_servers,
    reload_config,
    resolve_mcp_config_path,
    setup_logging,
)
from app.progenitor.llm.prompts import DEFAULT_PROMPTS_DIR
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
        }
        with patch.dict(os.environ, env, clear=False):
            config = LLMConfig.from_env()
            assert config.base_url == "https://example.openai.azure.com/openai/v1"
            assert config.api_key == "sk-test-key"
            assert config.model_default == "gpt-4o"

    def test_defaults(self):
        """Test default values."""
        config = LLMConfig()
        assert config.base_url == ""
        assert config.model_default == "gpt-5.1-codex-max"

    def test_get_model_for_role(self):
        """Test per-role model selection."""
        config = LLMConfig(
            model_default="default-model",
            model_planner="planner-model",
            model_builder="builder-model",
        )
        assert config.get_model_for_role("planner") == "planner-model"
        assert config.get_model_for_role("builder") == "builder-model"
        assert config.get_model_for_role("summary") == "default-model"
        assert config.get_model_for_role("unknown") == "default-model"


class TestRuntimeConfig:
    """Tests for runtime configuration."""

    def test_defaults(self):
        """Test default values match spec."""
        config = RuntimeConfig()
        assert config.timezone == "Australia/Melbourne"
        assert config.idle_backoff_seconds == 30
        assert config.max_actions_per_cycle == 10
        assert config.log_level == "info"

    def test_from_env_overrides_defaults(self):
        """Test environment variables override defaults."""
        env = {
            "PROGENITOR_TIMEZONE": "UTC",
            "PROGENITOR_IDLE_BACKOFF_SECONDS": "60",
            "PROGENITOR_MAX_ACTIONS_PER_CYCLE": "20",
            "PROGENITOR_LOG_LEVEL": "debug",
        }
        with patch.dict(os.environ, env, clear=False):
            config = RuntimeConfig.from_env()
            assert config.timezone == "UTC"
            assert config.idle_backoff_seconds == 60
            assert config.max_actions_per_cycle == 20
            assert config.log_level == "debug"

    def test_from_env_with_file_fallback(self):
        """Test file config is used as fallback."""
        file_config = {
            "timezone": "Europe/London",
            "idle_backoff_seconds": 45,
        }
        # Clear env vars
        env_clear = {
            "PROGENITOR_TIMEZONE": "",
            "PROGENITOR_IDLE_BACKOFF_SECONDS": "",
        }
        with patch.dict(os.environ, env_clear, clear=False):
            os.environ.pop("PROGENITOR_TIMEZONE", None)
            os.environ.pop("PROGENITOR_IDLE_BACKOFF_SECONDS", None)
            config = RuntimeConfig.from_env(file_config)
            assert config.timezone == "Europe/London"
            assert config.idle_backoff_seconds == 45


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
    """Tests for Progenitor's live MCP config loader."""

    def test_resolve_mcp_config_path_uses_checked_in_default(self):
        repo_root = Path(__file__).resolve().parents[2]
        with patch.dict(os.environ, {}, clear=False):
            path = resolve_mcp_config_path()
        assert path == repo_root / "data" / "config" / "mcp.json"

    def test_load_mcp_servers_reads_mcp_servers_map(self, tmp_path):
        config_path = tmp_path / "mcp.json"
        config_path.write_text(
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
}""",
            encoding="utf-8",
        )

        with patch.dict(os.environ, {"JINA_API_KEY": "jina_test_key"}, clear=False):
            servers = load_mcp_servers(config_path)

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

    def test_load_mcp_servers_drops_headers_with_missing_env(self, tmp_path):
        config_path = tmp_path / "mcp.json"
        config_path.write_text(
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
}""",
            encoding="utf-8",
        )

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("JINA_API_KEY", None)
            servers = load_mcp_servers(config_path)

        assert len(servers) == 1
        assert servers[0].name == "jina"
        assert servers[0].headers == {}

    def test_load_reads_checked_in_file(self, tmp_path):
        mcp_dir = tmp_path / "data" / "config"
        mcp_dir.mkdir(parents=True)
        config_path = mcp_dir / "mcp.json"
        config_path.write_text(
            """{
  "mcpServers": {
    "jina": {
      "enabled": true,
      "transport": "http",
      "url": "https://mcp.jina.ai/v1"
    }
  }
}""",
            encoding="utf-8",
        )

        config = MCPConfig.load(config_path=config_path)

        assert config.config_path == config_path
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
            "PROGENITOR_COPILOT_MODEL": "gpt-5",
            "PROGENITOR_COPILOT_TIMEOUT_SECONDS": "600",
        }

        with patch.dict(os.environ, env, clear=False):
            config = ProgenitorConfig.load(Path("."), Path("."))

            assert config.runtime.timezone == "Asia/Tokyo"
            assert config.runtime.idle_backoff_seconds == 15
            assert config.copilot.model == "gpt-5"
            assert config.copilot.timeout_seconds == 600

    def test_to_dict_redacts_secrets(self):
        """Test secrets are redacted in dict output."""
        config = ProgenitorConfig(
            llm=LLMConfig(
                api_key="sk-secret-key",
            )
        )

        result = config.to_dict(redact_secrets=True)

        assert result["llm"]["api_key"] == "***REDACTED***"

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
