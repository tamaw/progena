import importlib.util
import json
from pathlib import Path


def _load_script_module():
    script_path = Path(__file__).resolve().parents[2] / ".github" / "scripts" / "register_litellm_agent.py"
    spec = importlib.util.spec_from_file_location("register_litellm_agent", script_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_existing_agent_is_deleted_then_recreated(monkeypatch):
    module = _load_script_module()
    calls = []

    monkeypatch.setenv("AGENT_NAME", "builder_agent")
    monkeypatch.setenv("LITELLM_MASTER_KEY", "test-key")
    monkeypatch.setenv("LITELLM_BASE_URL", "http://litellm:4000")
    monkeypatch.setenv("AGENT_HOST", "agent-host")
    monkeypatch.setenv("AGENT_PORT", "8080")
    monkeypatch.setattr(module.sys, "argv", ["register_litellm_agent.py"])

    def fake_call(method, url, api_key, payload=None):  # noqa: ANN001
        calls.append((method, url, payload))
        if method == "GET" and url.endswith("/v1/agents"):
            return 200, json.dumps([{"agent_name": "builder_agent", "agent_id": "old-id"}])
        if method == "GET" and url.endswith("/.well-known/agent.json"):
            return 200, json.dumps(
                {
                    "name": "builder_agent",
                    "description": "builder",
                    "skills": [],
                    "defaultInputModes": ["text"],
                    "defaultOutputModes": ["text"],
                }
            )
        if method == "DELETE" and url.endswith("/v1/agents/old-id"):
            return 204, ""
        if method == "POST" and url.endswith("/v1/agents"):
            return 200, json.dumps({"agent_id": "new-id", "agent_name": "builder_agent"})
        if method == "GET" and url.endswith("/a2a/new-id/.well-known/agent.json"):
            return 200, json.dumps(
                {
                    "name": "builder_agent",
                    "description": "builder",
                    "skills": [],
                    "defaultInputModes": ["text"],
                    "defaultOutputModes": ["text"],
                }
            )
        raise AssertionError(f"Unexpected call: {method} {url}")

    monkeypatch.setattr(module, "call_via_docker_curl", fake_call)
    assert module.main() == 0

    methods = [method for method, _, _ in calls]
    assert "DELETE" in methods
    assert methods.index("DELETE") < methods.index("POST")
