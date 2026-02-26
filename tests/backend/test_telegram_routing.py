from app.backend.main import (
    _looks_like_spawn_github_agent_intent,
    _prepare_telegram_forward_message,
)


def test_spawn_github_agent_intent_detection() -> None:
    assert _looks_like_spawn_github_agent_intent("Please spawn a github agent for a PR")
    assert not _looks_like_spawn_github_agent_intent("what is the weather")


def test_prepare_telegram_forward_message_for_spawn_intent() -> None:
    message = "dispatch github agent to update docs"
    metadata = {"chat_id": "123", "username": "alice"}
    output = _prepare_telegram_forward_message(message, metadata)

    assert "spawn_github_agent" in output
    assert "chat_id=123" in output
    assert "username=alice" in output


def test_prepare_telegram_forward_message_passthrough() -> None:
    message = "hello there"
    output = _prepare_telegram_forward_message(message, {})
    assert output == message
