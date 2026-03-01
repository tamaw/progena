import json

import pytest

from app.agents.observe_agent.agent import agent, agent_card


@pytest.mark.asyncio
async def test_observe_agent_card_matches_a2a_agent_contract() -> None:
    response = await agent_card()
    payload = json.loads(response.body.decode("utf-8"))

    assert payload["protocolVersion"] == "1.0"
    assert payload["name"] == agent.AGENT_NAME
    assert payload["url"].endswith("/")
    assert payload["defaultInputModes"] == ["text"]
    assert payload["defaultOutputModes"] == ["text"]
    assert isinstance(payload["capabilities"], dict)
    assert isinstance(payload["skills"], list)
    skill_ids = {s.get("id") for s in payload["skills"] if isinstance(s, dict)}
    assert set(agent.CAPABILITIES).issubset(skill_ids)
