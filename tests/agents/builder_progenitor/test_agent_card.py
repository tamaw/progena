"""Tests for the Builder agent card."""

from app.agents.builder_progenitor.agent import AGENT_CARD


def test_agent_card_advertises_required_build_skills():
    skill_ids = {skill.id for skill in AGENT_CARD.skills}
    assert {"pr", "validate"}.issubset(skill_ids)
