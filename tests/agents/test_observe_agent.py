import json
from pathlib import Path

import pytest

from app.agents.observe_agent.agent import ObserveAgent


@pytest.mark.asyncio
async def test_observe_agent_uses_llm_and_tools(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    logs_dir = tmp_path / "logs"
    state_dir.mkdir()
    logs_dir.mkdir()

    (state_dir / "health.json").write_text(
        json.dumps({"status": "error", "error": "boom", "cycle_count": 12})
    )
    (logs_dir / "decisions.jsonl").write_text(
        json.dumps({"event": "planning_error", "error": "something broke"}) + "\n"
    )

    class FakeObserveAgent(ObserveAgent):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.calls = 0

        async def _post_responses(self, _payload):  # type: ignore[override]
            self.calls += 1
            if self.calls == 1:
                return {
                    "output_text": "",
                    "output": [
                        {
                            "type": "tool_call",
                            "call_id": "1",
                            "name": "read_state_json",
                            "arguments": json.dumps({"filename": "health.json"}),
                        }
                    ],
                }
            return {"output_text": "Progenitor is in error because health.json reports boom."}

    agent = FakeObserveAgent(state_dir=state_dir, logs_dir=logs_dir)
    text = await agent.process("why did you error last time", {})
    assert "health.json" in agent.tool_read_state_json("health.json")["path"]
    assert "boom" in text.lower()


def test_observe_agent_accepts_full_responses_base_url(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv(
        "AZURE_OPENAI_BASE_URL",
        "https://example.openai.azure.com/openai/v1/responses?api-version=preview",
    )
    agent = ObserveAgent(state_dir=tmp_path / "state", logs_dir=tmp_path / "logs")
    assert (
        agent._responses_endpoint()
        == "https://example.openai.azure.com/openai/v1/responses?api-version=preview"
    )


def test_observe_agent_tool_defs_match_responses_api_schema(tmp_path: Path) -> None:
    agent = ObserveAgent(state_dir=tmp_path / "state", logs_dir=tmp_path / "logs")
    tools = agent._tool_defs()

    assert tools
    for tool in tools:
        assert tool["type"] == "function"
        assert "function" not in tool
        assert isinstance(tool.get("name"), str) and tool["name"]
        assert isinstance(tool.get("description"), str) and tool["description"]
        assert tool.get("strict") is True
        parameters = tool.get("parameters")
        assert isinstance(parameters, dict)
        assert parameters.get("type") == "object"
        assert parameters.get("additionalProperties") is False
