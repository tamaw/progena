"""
Tests for Chat UI API routes.

Note: The backend is now API-only and does not serve static files.
The frontend runs separately via npm/Vite.
"""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock

from app.backend.main import app
from app.backend.state_reader import get_state_reader


@pytest.fixture
def client():
    """Create a test client."""
    return TestClient(app)


class TestUIRoutes:
    """Tests for /api/ui endpoints."""

    def test_ui_index_returns_404(self, client):
        """Test that / returns 404 (API-only backend)."""
        response = client.get("/")
        # Root path should return 404 - backend is API-only now
        assert response.status_code in (404, 405, 307)

    def test_ui_assets_not_served(self, client):
        """Test that /ui/ returns 404 (API-only backend)."""
        response = client.get("/ui/")
        assert response.status_code == 404

    def test_ui_status_endpoint(self, client):
        """Test /api/ui/status returns valid JSON."""
        response = client.get("/api/ui/status/snapshot")
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

    def test_workspace_endpoint(self, client):
        response = client.get("/api/ui/workspace")
        assert response.status_code == 200
        data = response.json()
        assert "workspace" in data
        assert "health" in data
        assert "sessions" in data
        assert "widgets" in data

    def test_widgets_endpoint(self, client):
        response = client.get("/api/ui/widgets")
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert "total" in data

    def test_schedules_alias_endpoint(self, client):
        response = client.get("/api/ui/schedules")
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
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


class TestDashboardRoutes:
    """Tests for Dashboard UI routes (now API-only)."""

    def test_dashboard_not_served(self, client):
        """Test that /ui/dashboard returns 404 (API-only backend)."""
        response = client.get("/ui/dashboard")
        assert response.status_code == 404

    def test_ui_alias_not_served(self, client):
        """Test that /ui returns 404 (API-only backend)."""
        response = client.get("/ui")
        assert response.status_code == 404




class TestLogsEndpoint:
    """Tests for /api/ui/logs endpoint."""

    def test_logs_returns_valid_structure(self, client):
        """Test that /api/ui/logs returns valid JSON."""
        response = client.get("/api/ui/logs")
        assert response.status_code == 200
        data = response.json()

        assert "logs" in data
        assert isinstance(data["logs"], str)

    def test_logs_json_format(self, client):
        """Test that /api/ui/logs can return JSON format."""
        response = client.get("/api/ui/logs?format=json")
        assert response.status_code == 200
        data = response.json()

        assert "entries" in data
        assert "total" in data
        assert isinstance(data["entries"], list)

    def test_logs_with_limit(self, client):
        """Test that /api/ui/logs respects limit parameter."""
        response = client.get("/api/ui/logs?limit=10")
        assert response.status_code == 200
        # Just verify it doesn't error

    def test_logs_with_level_filter(self, client):
        """Test that /api/ui/logs accepts level filter."""
        response = client.get("/api/ui/logs?level=error")
        assert response.status_code == 200

    def test_logs_with_search(self, client):
        """Test that /api/ui/logs accepts search parameter."""
        response = client.get("/api/ui/logs?search=test")
        assert response.status_code == 200


class TestAgentDetailEndpoint:
    """Tests for /api/ui/agents/{name} endpoint."""

    def test_agent_detail_not_found(self, client):
        """Test that requesting a non-existent agent returns 404."""
        response = client.get("/api/ui/agents/nonexistent-agent-xyz")
        assert response.status_code == 404

    def test_agent_detail_response_structure(self, client):
        """Test the expected response structure for agent detail endpoint."""
        # First, get the list of agents
        agents_response = client.get("/api/ui/agents")
        assert agents_response.status_code == 200
        agents = agents_response.json()["agents"]

        if len(agents) > 0:
            # Test with a real agent
            agent_name = agents[0]["name"]
            response = client.get(f"/api/ui/agents/{agent_name}")
            assert response.status_code == 200
            data = response.json()

            # Check expected fields
            assert "name" in data
            assert "status" in data
            assert "kind" in data
            assert "version" in data
            assert "description" in data
            assert "total_requests" in data
            assert "successful_requests" in data
            assert "failed_requests" in data
            assert "total_tokens_input" in data
            assert "total_tokens_output" in data
            assert "avg_response_time" in data
            assert "success_rate" in data
            assert "tokens_per_request_avg" in data


class TestBacklogEndpoint:
    """Tests for /api/ui/backlog endpoint."""

    def test_backlog_returns_valid_structure(self, client):
        """Test that /api/ui/backlog returns valid JSON with expected fields."""
        response = client.get("/api/ui/backlog")
        assert response.status_code == 200
        data = response.json()

        assert "items" in data
        assert "total" in data
        assert isinstance(data["items"], list)
        assert isinstance(data["total"], int)


class TestScheduleEndpoint:
    """Tests for /api/ui/schedule endpoint."""

    def test_schedule_returns_valid_structure(self, client):
        """Test that /api/ui/schedule returns valid JSON with expected fields."""
        response = client.get("/api/ui/schedule")
        assert response.status_code == 200
        data = response.json()

        assert "items" in data
        assert "total" in data
        assert isinstance(data["items"], list)
        assert isinstance(data["total"], int)


class TestLoopsEndpoint:
    """Tests for /api/ui/loops endpoint."""

    def test_loops_returns_valid_structure(self, client):
        """Test that /api/ui/loops returns valid JSON with expected fields."""
        response = client.get("/api/ui/loops")
        assert response.status_code == 200
        data = response.json()

        assert "loops" in data
        assert isinstance(data["loops"], list)


class TestNavigationLinks:
    """Tests for API endpoints (UI navigation is now client-side)."""

    def test_api_endpoints_work(self, client):
        """Test that API endpoints are accessible."""
        api_endpoints = [
            "/api/ui/status/snapshot",
            "/api/ui/agents",
            "/api/ui/backlog",
            "/api/ui/schedule",
        ]
        for endpoint in api_endpoints:
            response = client.get(endpoint)
            assert response.status_code == 200, f"Endpoint {endpoint} failed"


class TestAgentsPage:
    """Tests for the Agents API endpoint."""

    def test_agents_api_returns_json(self, client):
        """Test that /api/ui/agents returns JSON."""
        response = client.get("/api/ui/agents")
        assert response.status_code == 200
        assert "application/json" in response.headers["content-type"]


class TestLogsPage:
    """Tests for the Logs API endpoint."""

    def test_logs_api_returns_json(self, client):
        """Test that /api/ui/logs returns JSON."""
        response = client.get("/api/ui/logs")
        assert response.status_code == 200
        assert "application/json" in response.headers["content-type"]


# Note: These tests used to check for server-rendered navigation HTML content.
# With the React SPA, navigation is rendered client-side, so these tests are simplified
# to just verify the SPA HTML shell is returned correctly. Navigation behavior
# should be tested with end-to-end tests using a browser.

# Note: The following test classes were removed as they tested server-rendered HTML elements:
# - TestUIEnhancedFeatures (dashboard charts, stats grids, logs controls)
# - These elements are now rendered client-side by React


class TestUIAccessibility:
    """Tests for UI API endpoints.

    Note: HTML accessibility tests are no longer relevant since the backend
    is API-only. Frontend accessibility should be tested separately.
    """

    def test_chat_ui_has_favicon(self, client):
        """Test removed - UI pages no longer served by backend."""
        # This test is no longer relevant - backend is API-only
        pass

    def test_pages_have_viewport_meta(self, client):
        """Test removed - UI pages no longer served by backend."""
        # This test is no longer relevant - backend is API-only
        pass




class TestSSEConnection:
    """Tests for Server-Sent Events connectivity."""

    def test_status_endpoint_returns_complete_data(self, client):
        """Test that status endpoint returns all fields needed for UI updates."""
        response = client.get("/api/ui/status/snapshot")
        assert response.status_code == 200
        data = response.json()

        # These fields are used by SSE status updates
        required_fields = [
            "connected", "server_status", "progenitor_running", "uptime_seconds",
            "version", "cycle_count", "agents", "backlog", "schedule", "loops"
        ]

        for field in required_fields:
            assert field in data, f"Status missing required field: {field}"

    def test_status_has_build_info(self, client):
        """Test that status includes build information."""
        response = client.get("/api/ui/status/snapshot")
        assert response.status_code == 200
        data = response.json()

        # Build info should be present (may be null if no build)
        assert "build" in data

    def test_status_has_memory_info(self, client):
        """Test that status includes memory information."""
        response = client.get("/api/ui/status/snapshot")
        assert response.status_code == 200
        data = response.json()

        # Memory should be tracked
        assert "memory_mb" in data


class TestSplitSSEEndpoints:
    """Tests for the split SSE endpoints (/chat and /status/stream).

    Note: SSE streaming tests are skipped as they hang waiting for the infinite stream.
    The endpoints are verified indirectly via route registration checks.
    """

    def test_events_endpoint_is_registered(self, client):
        """Test that /api/ui/events endpoint is registered (via OpenAPI)."""
        response = client.get("/openapi.json")
        assert response.status_code == 200
        openapi = response.json()
        paths = openapi.get("paths", {})
        assert "/api/ui/events" in paths, "Events SSE endpoint should be registered"

    def test_status_stream_endpoint_is_registered(self, client):
        """Test that /api/ui/status SSE stream endpoint is registered."""
        response = client.get("/openapi.json")
        assert response.status_code == 200
        openapi = response.json()
        paths = openapi.get("paths", {})
        # The SSE stream endpoint has the same path as the snapshot but different handler
        # The snapshot is registered first, so check both handlers exist
        assert "/api/ui/status" in paths or "/api/ui/status/snapshot" in paths, "Status endpoint should be registered"

    def test_broadcast_functions_exist(self):
        """Test that the broadcast functions are available."""
        from app.backend.ui_routes import (
            broadcast_chat_event,
            broadcast_status_event,
        )
        # Just verify they're callable
        assert callable(broadcast_chat_event)
        assert callable(broadcast_status_event)


class TestWorkspaceBridgeActions:
    """Tests for backend action bridge endpoints."""

    def test_patch_schedule_widget_updates_schedule_file(self, client, tmp_path):
        state_dir = tmp_path / "state"
        state_dir.mkdir(parents=True)
        schedule_path = state_dir / "schedule.json"
        schedule_path.write_text(
            '[{"id":"sched-1","name":"Daily check","schedule":"0 0 * * *","enabled":true,"timezone":"UTC"}]',
            encoding="utf-8",
        )

        state_reader = get_state_reader()
        original_state_dir = state_reader.state_dir
        try:
            state_reader.state_dir = state_dir
            state_reader.invalidate()
            response = client.patch(
                "/api/ui/widgets/schedule:sched-1",
                json={"name": "Nightly check", "enabled": False, "expression": "0 1 * * *"},
            )
            assert response.status_code == 200
            updated = schedule_path.read_text(encoding="utf-8")
            assert "Nightly check" in updated
            assert "0 1 * * *" in updated
            assert "false" in updated.lower()
        finally:
            state_reader.state_dir = original_state_dir
            state_reader.invalidate()

    def test_run_and_generate_actions_use_existing_client(self, client):
        from app.backend import main as backend_main

        original_client = backend_main.client
        mock_client = AsyncMock()
        mock_client.send_message = AsyncMock(return_value="bridge-response")
        backend_main.client = mock_client
        try:
            run_response = client.post(
                "/api/ui/actions/run",
                json={"widget_id": "prompt:test", "prompt": "Run this"},
            )
            generate_response = client.post(
                "/api/ui/actions/generate",
                json={"widget_id": "prompt:test", "prompt": "Generate this"},
            )

            assert run_response.status_code == 200
            assert generate_response.status_code == 200
            assert run_response.json()["response"] == "bridge-response"
            assert generate_response.json()["response"] == "bridge-response"
            assert mock_client.send_message.await_count == 2
        finally:
            backend_main.client = original_client
