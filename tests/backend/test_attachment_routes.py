"""Tests for thread-scoped access control on /api/attachments.

Regression coverage for the cross-thread attachment leak: an attachment
uploaded/claimed by one thread must be invisible (list), unreadable
(download), and undeletable through another thread's requests. See
app/backend/attachment_routes.py and app/backend/attachment_blob_store.py.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.backend.attachment_blob_store import (
    InMemoryAttachmentBlobStore,
    set_default_attachment_blob_store,
)
from app.backend.attachment_routes import get_attachment_manager
from app.backend.main import app


@pytest.fixture
def client():
    set_default_attachment_blob_store(InMemoryAttachmentBlobStore())
    get_attachment_manager().clear_cache()
    return TestClient(app)


def _upload(client: TestClient, *, filename: str, thread_id: str | None = None) -> dict:
    # Unique body per call so AttachmentManager's checksum-based dedup never
    # collides two calls into "the same attachment" within a test run.
    content = f"hello world {uuid.uuid4()}".encode()
    files = {"file": (filename, content, "text/plain")}
    data = {"thread_id": thread_id} if thread_id else {}
    response = client.post("/api/attachments", files=files, data=data)
    assert response.status_code == 200, response.text
    return response.json()


class TestListRequiresThread:
    def test_list_without_thread_id_is_rejected(self, client):
        response = client.get("/api/attachments")
        assert response.status_code == 422

    def test_list_only_returns_the_requesting_threads_attachments(self, client):
        _upload(client, filename="mine.txt", thread_id="thread-a")
        _upload(client, filename="theirs.txt", thread_id="thread-b")

        response = client.get("/api/attachments", params={"thread_id": "thread-a"})
        assert response.status_code == 200
        names = [row["name"] for row in response.json()["attachments"]]
        assert names == ["mine.txt"]


class TestDownloadIsThreadScoped:
    def test_download_from_another_thread_is_404(self, client):
        uploaded = _upload(client, filename="secret.txt", thread_id="thread-a")

        response = client.get(
            f"/api/attachments/{uploaded['id']}", params={"thread_id": "thread-b"}
        )
        assert response.status_code == 404

    def test_download_from_owning_thread_succeeds(self, client):
        uploaded = _upload(client, filename="secret.txt", thread_id="thread-a")

        response = client.get(
            f"/api/attachments/{uploaded['id']}", params={"thread_id": "thread-a"}
        )
        assert response.status_code == 200
        assert response.content.startswith(b"hello world")

    def test_download_without_thread_id_is_rejected(self, client):
        uploaded = _upload(client, filename="secret.txt", thread_id="thread-a")

        response = client.get(f"/api/attachments/{uploaded['id']}")
        assert response.status_code == 422

    def test_unclaimed_attachment_is_not_downloadable_by_guessed_thread(self, client):
        """An attachment uploaded without a thread_id (e.g. mid-compose,
        before the message is sent) has no owner yet -- it must not be
        readable by any thread_id someone happens to pass."""
        uploaded = _upload(client, filename="pending.txt")

        response = client.get(
            f"/api/attachments/{uploaded['id']}", params={"thread_id": "thread-a"}
        )
        assert response.status_code == 404


class TestDeleteIsThreadScoped:
    def test_delete_from_another_thread_is_404_and_does_not_delete(self, client):
        uploaded = _upload(client, filename="secret.txt", thread_id="thread-a")

        response = client.delete(
            f"/api/attachments/{uploaded['id']}", params={"thread_id": "thread-b"}
        )
        assert response.status_code == 404

        # Still there, and still only visible to its owning thread.
        still_there = client.get(
            f"/api/attachments/{uploaded['id']}", params={"thread_id": "thread-a"}
        )
        assert still_there.status_code == 200

    def test_delete_from_owning_thread_succeeds(self, client):
        uploaded = _upload(client, filename="secret.txt", thread_id="thread-a")

        response = client.delete(
            f"/api/attachments/{uploaded['id']}", params={"thread_id": "thread-a"}
        )
        assert response.status_code == 204

        gone = client.get(f"/api/attachments/{uploaded['id']}", params={"thread_id": "thread-a"})
        assert gone.status_code == 404
