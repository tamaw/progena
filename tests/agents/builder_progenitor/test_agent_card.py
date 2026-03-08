"""Tests for the Builder agent card."""

from app.agents.builder_progenitor.agent import AGENT_CARD, AGENT_NAME, STEP_SKILLS


def test_agent_card_includes_expected_metadata():
    assert AGENT_CARD.name == AGENT_NAME
    assert AGENT_CARD.url.startswith("http://")
    assert len(AGENT_CARD.skills) == len(STEP_SKILLS)
    assert any(skill.id == "pr" for skill in AGENT_CARD.skills)
    assert any(skill.id == "validate" for skill in AGENT_CARD.skills)
