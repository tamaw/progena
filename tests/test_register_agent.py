"""Tests for .github/scripts/register_agent.py."""
import importlib.util
from pathlib import Path
from unittest.mock import patch

# Load the script as a module (it lives outside the package tree)
_SCRIPT_PATH = Path(__file__).parents[1] / ".github" / "scripts" / "register_agent.py"
_spec = importlib.util.spec_from_file_location("register_agent", _SCRIPT_PATH)
assert _spec and _spec.loader
_register_agent = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_register_agent)  # type: ignore[union-attr]


class TestUpdateRegistryInContainer:
    def test_runs_docker_exec_with_expected_env(self):
        with patch.object(_register_agent.subprocess, "run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "ok"
            mock_run.return_value.stderr = ""

            _register_agent._update_registry_in_container(
                progenitor_container="progenitor",
                registry_path="/app/data/config/agents.json",
                agent_name="my_agent",
                port=8100,
                docker_hostname="abcd1234",
            )

            cmd = mock_run.call_args.args[0]
            assert cmd[0:3] == ["docker", "exec", "-e"]
            assert "AGENT_NAME=my_agent" in cmd
            assert "PORT=8100" in cmd
            assert "DOCKER_HOSTNAME=abcd1234" in cmd
            assert "REGISTRY_PATH=/app/data/config/agents.json" in cmd
            assert "progenitor" in cmd
            assert _register_agent._UPDATE_REGISTRY_SCRIPT in cmd

    def test_raises_when_docker_exec_fails(self):
        with patch.object(_register_agent.subprocess, "run") as mock_run:
            mock_run.return_value.returncode = 1
            mock_run.return_value.stdout = ""
            mock_run.return_value.stderr = "permission denied"
            try:
                _register_agent._update_registry_in_container(
                    progenitor_container="progenitor",
                    registry_path="/app/data/config/agents.json",
                    agent_name="my_agent",
                    port=8100,
                    docker_hostname="abcd1234",
                )
            except RuntimeError as exc:
                assert "permission denied" in str(exc)
            else:
                raise AssertionError("Expected RuntimeError")


class TestMain:
    def _run_main(self, env: dict) -> int:
        with patch.dict("os.environ", env, clear=False):
            return _register_agent.main()

    def test_fails_without_agent_name(self):
        with patch.dict("os.environ", {"AGENT_NAME": "", "PORT": "8100"}, clear=False):
            result = _register_agent.main()
        assert result == 1

    def test_fails_without_port(self):
        with patch.dict("os.environ", {"AGENT_NAME": "my_agent", "PORT": ""}, clear=False):
            result = _register_agent.main()
        assert result == 1

    def test_fails_without_docker_hostname(self):
        with (
            patch.dict("os.environ", {"AGENT_NAME": "my_agent", "PORT": "8100", "DOCKER_HOSTNAME": ""}, clear=False),
        ):
            result = _register_agent.main()
        assert result == 1

    def test_calls_update_registry_helper(self):
        with patch.object(_register_agent, "_update_registry_in_container") as update:
            code = self._run_main(
                {
                    "AGENT_NAME": "my_agent",
                    "PORT": "8100",
                    "DOCKER_HOSTNAME": "docker-host-123",
                }
            )
        assert code == 0
        update.assert_called_once_with(
            progenitor_container="progenitor",
            registry_path="/app/data/config/agents.json",
            agent_name="my_agent",
            port=8100,
            docker_hostname="docker-host-123",
        )

    def test_returns_error_when_update_fails(self):
        with patch.object(_register_agent, "_update_registry_in_container", side_effect=RuntimeError("boom")):
            code = self._run_main(
                {
                    "AGENT_NAME": "my_agent",
                    "PORT": "8100",
                    "DOCKER_HOSTNAME": "docker-host-123",
                }
            )
        assert code == 1
