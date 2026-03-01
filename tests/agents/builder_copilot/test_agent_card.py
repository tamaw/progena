"""Tests for the Builder Copilot agent card."""

from app.agents.builder_copilot.agent import AGENT_NAME, STEP_SKILLS, build_agent_card_payload


def test_agent_card_payload_includes_expected_metadata():
    payload = build_agent_card_payload(8108, "localhost")

    assert payload["name"] == AGENT_NAME
    assert payload["url"] == "http://localhost:8108/"
    assert len(payload["skills"]) == len(STEP_SKILLS)
    assert any(skill["id"] == "pr" for skill in payload["skills"])
