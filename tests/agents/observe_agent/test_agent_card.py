import json

import pytest

from app.agents.observe_agent.agent import agent, agent_card


@pytest.mark.asyncio
async def test_observe_agent_card_matches_a2a_agent_contract() -> None:
    response = await agent_card()
    payload = json.loads(response.body.decode("utf-8"))

    assert payload["defaultInputModes"] == ["text/plain"]
    assert payload["defaultOutputModes"] == ["text/plain"]
    assert payload["capabilities"]["stateTransitionHistory"] is True
    skill_ids = {skill.get("id") for skill in payload["skills"] if isinstance(skill, dict)}
    assert set(agent.CAPABILITIES).issubset(skill_ids)
