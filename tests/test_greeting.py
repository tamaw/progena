import logging
from app.agents.greeting_agent.agent import Agent


def test_simple_greeting(caplog):
    caplog.set_level(logging.INFO)
    agent = Agent()
    context = {
        "work_item_id": "wi-98ae4039",
        "a2a_context_id": "4d983afd-c591-4a93-b1c5-e71d5ceeafb6",
    }

    result = agent.handle("hi", context)

    assert "How can I help" in result["response"]

    # Ensure logging contains the correlation metadata
    found = any(
        ("wi-98ae4039" in rec.getMessage() and "4d983afd-c591-4a93-b1c5-e71d5ceeafb6" in rec.getMessage())
        for rec in caplog.records
    )
    assert found, "Expected log entry with work_item_id and a2a_context_id"
