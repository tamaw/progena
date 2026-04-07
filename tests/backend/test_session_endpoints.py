"""Tests for session and conversation reset endpoints."""

import pytest


@pytest.fixture(autouse=True)
def reset_backend_state(mock_progenitor_client):
    """Reset shared backend globals between tests."""
    from app.backend import main

    main.session_manager._sessions.clear()
    main.session_manager._user_sessions.clear()
    main.conversation_history.clear()
    mock_progenitor_client.reset_conversation.reset_mock()
    mock_progenitor_client.reset_conversation.return_value = {
        "reset": True,
        "run_id": "run_mocked",
        "new_run_id": "run_mocked",
        "previous_run_id": "run_previous",
        "messages_cleared": 0,
    }
    yield
    main.session_manager._sessions.clear()
    main.session_manager._user_sessions.clear()
    main.conversation_history.clear()


def test_get_session_creates_cookie_and_resets_runtime_once(client, mock_progenitor_client):
    response = client.get("/v1/session")

    assert response.status_code == 200
    assert response.json()["conversation_id"] == "run_mocked"
    assert "progenitor_session=" in response.headers["set-cookie"]
    mock_progenitor_client.reset_conversation.assert_awaited_once()

    second = client.get("/v1/session")

    assert second.status_code == 200
    assert second.json()["session_id"] == response.json()["session_id"]
    mock_progenitor_client.reset_conversation.assert_awaited_once()


def test_post_session_always_creates_fresh_runtime_conversation(client, mock_progenitor_client):
    first = client.post("/v1/session")
    second = client.post("/v1/session")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["session_id"] != second.json()["session_id"]
    assert first.json()["conversation_id"] == "run_mocked"
    assert second.json()["conversation_id"] == "run_mocked"
    assert mock_progenitor_client.reset_conversation.await_count == 2


def test_delete_conversation_clears_backend_state_and_updates_session(
    client, mock_progenitor_client
):
    from app.backend import main

    session_response = client.get("/v1/session")
    session_id = session_response.json()["session_id"]
    session = main.session_manager._sessions[session_id]
    session.message_count = 7
    session.pending_attachments.extend(["att_1", "att_2"])
    main.conversation_history.add_message("user", "hello")

    mock_progenitor_client.reset_conversation.reset_mock()
    mock_progenitor_client.reset_conversation.return_value = {
        "reset": True,
        "run_id": "run_after_clear",
        "new_run_id": "run_after_clear",
        "previous_run_id": "run_mocked",
        "messages_cleared": 2,
    }

    response = client.delete("/v1/conversation")

    assert response.status_code == 200
    assert response.json() == {
        "status": "cleared",
        "conversation_id": "run_after_clear",
    }
    assert len(main.conversation_history) == 0
    assert session.message_count == 0
    assert session.pending_attachments == []
    assert session.conversation_id == "run_after_clear"
    mock_progenitor_client.reset_conversation.assert_awaited_once()
