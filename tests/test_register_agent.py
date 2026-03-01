"""Tests for .github/scripts/register_agent.py."""
import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Load the script as a module (it lives outside the package tree)
_SCRIPT_PATH = Path(__file__).parents[1] / ".github" / "scripts" / "register_agent.py"
_spec = importlib.util.spec_from_file_location("register_agent", _SCRIPT_PATH)
assert _spec and _spec.loader
_register_agent = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_register_agent)  # type: ignore[union-attr]


class TestLoadRegistry:
    def test_returns_empty_registry_when_file_missing(self, tmp_path):
        result = _register_agent._load_registry(tmp_path / "agents.json")
        assert result == {"agents": []}

    def test_loads_valid_file(self, tmp_path):
        path = tmp_path / "agents.json"
        path.write_text(json.dumps({"agents": [{"name": "foo"}]}))
        result = _register_agent._load_registry(path)
        assert result["agents"] == [{"name": "foo"}]

    def test_returns_empty_on_invalid_json(self, tmp_path):
        path = tmp_path / "agents.json"
        path.write_text("not-json")
        result = _register_agent._load_registry(path)
        assert result == {"agents": []}


class TestSaveRegistry:
    def test_saves_and_creates_parent_dirs(self, tmp_path):
        path = tmp_path / "sub" / "agents.json"
        data = {"agents": [{"name": "bar"}]}
        _register_agent._save_registry(path, data)
        assert path.exists()
        assert json.loads(path.read_text()) == data

    def test_overwrites_existing_file(self, tmp_path):
        path = tmp_path / "agents.json"
        path.write_text(json.dumps({"agents": [{"name": "old"}]}))
        _register_agent._save_registry(path, {"agents": []})
        assert json.loads(path.read_text()) == {"agents": []}


class TestFetchAgentCard:
    def test_uses_container_hostname_in_request(self):
        """_fetch_agent_card should use the container name (not localhost) as hostname."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '{"name": "my_agent", "version": "1.0.0"}'
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            _register_agent._fetch_agent_card("progenitor-agent-my_agent", 8100)
        call_args = mock_run.call_args[0][0]
        # The python -c script should reference the container hostname, not localhost
        script_arg = call_args[-1]
        assert "progenitor-agent-my_agent:8100" in script_arg
        assert "localhost" not in script_arg

    def test_returns_none_on_nonzero_exit(self):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "error"
        mock_result.stdout = ""
        with patch("subprocess.run", return_value=mock_result):
            result = _register_agent._fetch_agent_card("progenitor-agent-my_agent", 8100)
        assert result is None

    def test_returns_none_on_invalid_json(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "not-json"
        with patch("subprocess.run", return_value=mock_result):
            result = _register_agent._fetch_agent_card("progenitor-agent-my_agent", 8100)
        assert result is None

    def test_returns_parsed_card(self):
        card = {"name": "my_agent", "version": "1.0.0", "skills": [{"id": "foo"}]}
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(card)
        with patch("subprocess.run", return_value=mock_result):
            result = _register_agent._fetch_agent_card("progenitor-agent-my_agent", 8100)
        assert result == card


class TestBuildEntry:
    def test_uses_defaults_when_card_is_none(self):
        entry = _register_agent._build_entry("my_agent", 8100, None, None)
        assert entry["name"] == "my_agent"
        assert entry["port"] == 8100
        assert entry["kind"] == "a2a_agent"
        assert entry["transport"] == "http"
        assert entry["endpoint"] == "http://progenitor-agent-my_agent:8100"
        assert entry["agent_card_url"] == "http://progenitor-agent-my_agent:8100/.well-known/agent.json"
        assert entry["status"] == "running"
        assert entry["enabled"] is True
        assert entry["capabilities"] == []

    def test_extracts_metadata_from_card(self):
        card = {
            "name": "My Agent",
            "description": "Does things",
            "version": "1.2.3",
            "skills": [{"id": "skill-a"}, {"id": "skill-b"}],
        }
        entry = _register_agent._build_entry("my_agent", 8100, card, None)
        assert entry["description"] == "Does things"
        assert entry["version"] == "1.2.3"
        assert set(entry["capabilities"]) == {"skill-a", "skill-b"}

    def test_preserves_created_at_from_existing(self):
        existing = {"name": "my_agent", "created_at": "2024-01-01T00:00:00", "entrypoint": "app/agents/my_agent/agent.py"}
        entry = _register_agent._build_entry("my_agent", 8100, None, existing)
        assert entry["created_at"] == "2024-01-01T00:00:00"

    def test_preserves_entrypoint_from_existing(self):
        existing = {"name": "my_agent", "created_at": "2024-01-01T00:00:00", "entrypoint": "app/agents/my_agent/http_agent.py"}
        entry = _register_agent._build_entry("my_agent", 8100, None, existing)
        assert entry["entrypoint"] == "app/agents/my_agent/http_agent.py"


class TestMain:
    def _run_main(self, env: dict, tmp_path: Path) -> int:
        registry = tmp_path / "agents.json"
        env.setdefault("REGISTRY_FILE", str(registry))
        with patch.dict("os.environ", env, clear=False):
            with patch.object(_register_agent, "_fetch_agent_card", return_value=None):
                return _register_agent.main()

    def test_fails_without_agent_name(self, tmp_path):
        with patch.dict("os.environ", {"AGENT_NAME": "", "PORT": "8100"}, clear=False):
            result = _register_agent.main()
        assert result == 1

    def test_fails_without_port(self, tmp_path):
        with patch.dict("os.environ", {"AGENT_NAME": "my_agent", "PORT": ""}, clear=False):
            result = _register_agent.main()
        assert result == 1

    def test_creates_registry_entry(self, tmp_path):
        code = self._run_main({"AGENT_NAME": "my_agent", "PORT": "8100"}, tmp_path)
        assert code == 0
        registry = json.loads((tmp_path / "agents.json").read_text())
        agents = registry["agents"]
        assert len(agents) == 1
        assert agents[0]["name"] == "my_agent"
        assert agents[0]["port"] == 8100
        assert agents[0]["status"] == "running"

    def test_replaces_existing_entry(self, tmp_path):
        registry_path = tmp_path / "agents.json"
        registry_path.write_text(json.dumps({
            "agents": [{"name": "my_agent", "port": 9999, "created_at": "2024-01-01T00:00:00"}]
        }))
        code = self._run_main({"AGENT_NAME": "my_agent", "PORT": "8100"}, tmp_path)
        assert code == 0
        registry = json.loads(registry_path.read_text())
        agents = registry["agents"]
        assert len(agents) == 1
        assert agents[0]["port"] == 8100
        assert agents[0]["created_at"] == "2024-01-01T00:00:00"

    def test_preserves_other_agents(self, tmp_path):
        registry_path = tmp_path / "agents.json"
        registry_path.write_text(json.dumps({
            "agents": [{"name": "other_agent", "port": 8200}]
        }))
        code = self._run_main({"AGENT_NAME": "my_agent", "PORT": "8100"}, tmp_path)
        assert code == 0
        registry = json.loads(registry_path.read_text())
        names = {a["name"] for a in registry["agents"]}
        assert names == {"my_agent", "other_agent"}
