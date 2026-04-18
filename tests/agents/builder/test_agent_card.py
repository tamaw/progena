"""Tests for the Builder agent card."""

from app.agents.builder.agent import AGENT_CARD, AGENT_NAME, STEP_SKILLS


def test_agent_card_includes_expected_metadata():
    assert AGENT_CARD.name == AGENT_NAME
    assert AGENT_CARD.url.startswith("http://")
    assert len(AGENT_CARD.skills) == len(STEP_SKILLS)
    skill_ids = {skill.id for skill in AGENT_CARD.skills}
    assert skill_ids == {"plan", "build", "validate"}
    assert "router" in AGENT_CARD.description.lower()
