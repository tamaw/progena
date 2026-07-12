"""Tests for the Builder Agent AgentCard and A2A metadata."""

from app.agents.builder_agent.agent import AGENT_CARD


def test_agent_card_has_required_fields():
    assert AGENT_CARD.name
    assert AGENT_CARD.description
    assert AGENT_CARD.url
    assert AGENT_CARD.version


def test_agent_card_has_skills():
    skill_ids = {s.id for s in AGENT_CARD.skills}
    assert "build_agent" in skill_ids
    assert "build_mcp" in skill_ids
    assert "upgrade_repo" in skill_ids


def test_agent_card_capabilities():
    caps = AGENT_CARD.capabilities
    assert caps is not None


def test_agent_card_advertises_streaming():
    # Required for the A2A client to negotiate the streaming transport at
    # all (a2a-sdk's base_client.py gates it on client.streaming AND
    # card.capabilities.streaming both being true) -- BuilderAgentExecutor
    # now runs each build inline for exactly this reason, so the terminal
    # and progress events it pushes can actually reach a streaming client.
    assert AGENT_CARD.capabilities.streaming is True


def test_agent_card_input_output_modes():
    assert "text/plain" in AGENT_CARD.default_input_modes
    assert "text/plain" in AGENT_CARD.default_output_modes


def test_agent_card_url_format():
    assert AGENT_CARD.url.startswith("http")
