"""
Tests for Chat UI API routes.
"""

import pytest
from fastapi.testclient import TestClient

from app.chat_server.main import app


@pytest.fixture
def client():
    """Create a test client."""
    return TestClient(app)


class TestUIRoutes:
    """Tests for /api/ui endpoints."""

    def test_ui_index_returns_html(self, client):
        """Test that /ui returns the HTML page."""
        response = client.get("/")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "Progenitor Chat" in response.text

    def test_ui_css_files_accessible(self, client):
        """Test that CSS files are accessible."""
        response = client.get("/css/main.css")
        assert response.status_code == 200
        assert "text/css" in response.headers["content-type"]

    def test_ui_js_files_accessible(self, client):
        """Test that JS files are accessible."""
        response = client.get("/js/config.js")
        assert response.status_code == 200
        assert "javascript" in response.headers["content-type"]

    def test_ui_status_endpoint(self, client):
        """Test /api/ui/status returns valid JSON."""
        response = client.get("/api/ui/status")
        assert response.status_code == 200
        data = response.json()
        assert "connected" in data
        assert "server_status" in data
        assert "progenitor_running" in data

    def test_ui_agents_endpoint(self, client):
        """Test /api/ui/agents returns agent list."""
        response = client.get("/api/ui/agents")
        assert response.status_code == 200
        data = response.json()
        assert "agents" in data
        assert "total" in data
        assert isinstance(data["agents"], list)

    def test_ui_tasks_endpoint(self, client):
        """Test /api/ui/tasks returns task list."""
        response = client.get("/api/ui/tasks")
        assert response.status_code == 200
        data = response.json()
        assert "tasks" in data
        assert "total" in data

    def test_ui_alerts_endpoint(self, client):
        """Test /api/ui/alerts returns alerts."""
        response = client.get("/api/ui/alerts")
        assert response.status_code == 200
        data = response.json()
        assert "alerts" in data
        assert "total" in data

    def test_dismiss_nonexistent_alert(self, client):
        """Test dismissing a non-existent alert returns 404."""
        response = client.post("/api/ui/alerts/nonexistent/dismiss")
        assert response.status_code == 404

    def test_respond_nonexistent_alert(self, client):
        """Test responding to a non-existent alert returns 404."""
        response = client.post("/api/ui/alerts/nonexistent/respond")
        assert response.status_code == 404
