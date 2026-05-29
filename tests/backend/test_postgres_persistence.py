from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from fastapi.testclient import TestClient

from app.backend.main import app
from app.backend.session import SessionConfig, SessionManager


@dataclass
class FakeRepository:
    messages: list[dict[str, Any]] = field(default_factory=list)
    cleared: list[str] = field(default_factory=list)

    async def add_message(
        self,
        conversation_id: str,
        *,
        role: str,
        content: str,
        message_id: str,
        checkpoint_thread_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.messages.append(
            {
                "id": message_id,
                "conversation_id": conversation_id,
                "role": role,
                "content": content,
                "checkpoint_thread_id": checkpoint_thread_id,
                "metadata": metadata or {},
                "created_at": datetime(2026, 1, 1, tzinfo=UTC),
            }
        )

    async def list_messages(self, conversation_id: str) -> list[dict[str, Any]]:
        return [
            {**message, "sequence": index + 1}
            for index, message in enumerate(self.messages)
            if message["conversation_id"] == conversation_id
        ]

    async def clear_conversation(self, conversation_id: str) -> None:
        self.cleared.append(conversation_id)
        self.messages = [
            message for message in self.messages if message["conversation_id"] != conversation_id
        ]


def test_chat_persists_exchange_to_repository(monkeypatch):
    import app.backend.main as backend_main

    fake_repo = FakeRepository()
    session_manager = SessionManager(SessionConfig(persist_dir=None))
    monkeypatch.setattr(backend_main, "repository", fake_repo)
    monkeypatch.setattr(backend_main, "session_manager", session_manager)

    with TestClient(app) as client:
        response = client.post("/chat", json={"message": "hello"})

    assert response.status_code == 200
    assert [message["role"] for message in fake_repo.messages] == ["user", "assistant"]
    assert fake_repo.messages[0]["content"] == "hello"
    assert fake_repo.messages[1]["content"] == "mocked response"
    assert fake_repo.messages[0]["conversation_id"].startswith("thread_")


def test_conversation_endpoint_reads_and_clears_repository(monkeypatch):
    import app.backend.main as backend_main

    fake_repo = FakeRepository()
    session_manager = SessionManager(SessionConfig(persist_dir=None))
    monkeypatch.setattr(backend_main, "repository", fake_repo)
    monkeypatch.setattr(backend_main, "session_manager", session_manager)

    with TestClient(app) as client:
        session_response = client.get("/v1/session")
        conversation_id = session_response.json()["conversation_id"]
        fake_repo.messages.append(
            {
                "id": "msg_1",
                "conversation_id": conversation_id,
                "role": "user",
                "content": "stored message",
                "checkpoint_thread_id": conversation_id,
                "metadata": {},
                "created_at": datetime(2026, 1, 1, tzinfo=UTC),
            }
        )

        response = client.get("/v1/conversation")
        clear_response = client.delete("/v1/conversation")

    assert response.status_code == 200
    assert response.json()["conversation_id"] == conversation_id
    assert response.json()["messages"][0]["content"] == "stored message"
    assert clear_response.status_code == 200
    assert fake_repo.cleared == [conversation_id]
