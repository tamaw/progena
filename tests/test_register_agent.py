"""Tests for .github/scripts/register_agent.py."""
import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

# Load the script as a module (it lives outside the package tree)
_SCRIPT_PATH = Path(__file__).parents[1] / ".github" / "scripts" / "register_agent.py"
_spec = importlib.util.spec_from_file_location("register_agent", _SCRIPT_PATH)
assert _spec and _spec.loader
_register_agent = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_register_agent)  # type: ignore[union-attr]


class TestLoadRegistry:
    def test_returns_empty_registry_when_file_missing(self, tmp_path):
        result = _register_agent._load_registry(tmp_path / "agents.json")
        assert result == {}

    def test_loads_valid_file(self, tmp_path):
        path = tmp_path / "agents.json"
        path.write_text(json.dumps({"foo": {"url": "http://foo"}}))
        result = _register_agent._load_registry(path)
        assert result["foo"]["url"] == "http://foo"

    def test_returns_empty_on_invalid_json(self, tmp_path):
        path = tmp_path / "agents.json"
        path.write_text("not-json")
        result = _register_agent._load_registry(path)
        assert result == {}


class TestSaveRegistry:
    def test_saves_and_creates_parent_dirs(self, tmp_path):
        path = tmp_path / "sub" / "agents.json"
        data = {"bar": {"url": "http://bar"}}
        _register_agent._save_registry(path, data)
        assert path.exists()
        assert json.loads(path.read_text()) == data

    def test_overwrites_existing_file(self, tmp_path):
        path = tmp_path / "agents.json"
        path.write_text(json.dumps({"old": {"url": "http://old"}}))
        _register_agent._save_registry(path, {})
        assert json.loads(path.read_text()) == {}


class TestBuildEntry:
    def test_uses_defaults_when_card_is_none(self):
        entry = _register_agent._build_entry("my_agent", 8100, "docker-host-123", None)
        assert entry == {"url": "http://docker-host-123:8100"}

    def test_preserves_headers_from_existing(self):
        existing = {
            "headers": {
                "Authorization": "Bearer abc",
                "X-Env": "dev",
            },
        }
        entry = _register_agent._build_entry("my_agent", 8100, "docker-host-123", existing)
        assert entry["headers"] == {"Authorization": "Bearer abc", "X-Env": "dev"}

    def test_does_not_keep_other_existing_fields(self):
        existing = {"entrypoint": "app/agents/my_agent/http_agent.py", "enabled": False}
        entry = _register_agent._build_entry("my_agent", 8100, "docker-host-123", existing)
        assert "entrypoint" not in entry
        assert "enabled" not in entry


class TestMain:
    def _run_main(self, env: dict, tmp_path: Path) -> int:
        registry = tmp_path / "agents.json"
        env.setdefault("REGISTRY_FILE", str(registry))
        env.setdefault("DOCKER_HOSTNAME", "docker-host-123")
        env.setdefault("CONTAINER_NAME", "progenitor-agent-my_agent")
        with (
            patch.dict("os.environ", env, clear=False),
            patch.object(_register_agent, "_check_agent_health", return_value=None),
        ):
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
        assert registry == {"my_agent": {"url": "http://docker-host-123:8100"}}

    def test_replaces_existing_entry(self, tmp_path):
        registry_path = tmp_path / "agents.json"
        registry_path.write_text(json.dumps({"my_agent": {"headers": {"Authorization": "Bearer token"}, "enabled": False}}))
        code = self._run_main({"AGENT_NAME": "my_agent", "PORT": "8100"}, tmp_path)
        assert code == 0
        registry = json.loads(registry_path.read_text())
        assert registry["my_agent"] == {
            "url": "http://docker-host-123:8100",
            "headers": {"Authorization": "Bearer token"},
        }

    def test_preserves_other_agents(self, tmp_path):
        registry_path = tmp_path / "agents.json"
        registry_path.write_text(json.dumps({"other_agent": {"url": "http://other"}}))
        code = self._run_main({"AGENT_NAME": "my_agent", "PORT": "8100"}, tmp_path)
        assert code == 0
        registry = json.loads(registry_path.read_text())
        names = set(registry.keys())
        assert names == {"my_agent", "other_agent"}

    def test_fails_when_health_check_fails(self, tmp_path):
        with (
            patch.dict("os.environ", {"AGENT_NAME": "my_agent", "PORT": "8100", "DOCKER_HOSTNAME": "docker-host-123"}, clear=False),
            patch.object(_register_agent, "_check_agent_health", side_effect=RuntimeError("boom")),
        ):
            result = _register_agent.main()
        assert result == 1
