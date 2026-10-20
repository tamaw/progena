"""Tests for the Builder Agent AgentCard and A2A metadata."""

from app.agents.builder_agent.agent import AGENT_CARD


def test_agent_card_has_skills():
    skill_ids = {s.id for s in AGENT_CARD.skills}
    assert "build_agent" in skill_ids
    assert "build_mcp" in skill_ids
    assert "upgrade_repo" in skill_ids


def test_agent_card_advertises_streaming():
    # Required for the A2A client to negotiate the streaming transport at
    # all (a2a-sdk's base_client.py gates it on client.streaming AND
    # card.capabilities.streaming both being true) -- BuilderAgentExecutor
    # now runs each build inline for exactly this reason, so the terminal
    # and progress events it pushes can actually reach a streaming client.
    assert AGENT_CARD.capabilities.streaming is True
