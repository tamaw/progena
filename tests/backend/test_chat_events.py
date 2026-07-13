"""
Tests for Chat Event Types and UI/UX Interactions.

Comprehensive tests for different types of chat events, SSE delivery,
event payloads, and user interaction flows.

Covers:
- User message events
- Assistant response events
- Streaming chunk events
- Error events
- Connection/disconnection events
- Event ordering and timing
"""

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.backend.main import app
from app.backend.models import (
    ResponseInputMessage,
    ResponseOutputMessage,
    ResponseOutputReasoning,
    ResponseOutputText,
    ResponsePayload,
    ResponseReasoningSummaryText,
    ResponseRequest,
    StreamingResponseRequest,
)


@pytest.fixture
def client():
    """Create a test client."""
    return TestClient(app)


class TestUserMessageEvents:
    """Tests for user message event handling."""

    def test_simple_user_message_accepted(self, client):
        """A chat request is an acknowledgement whose output is only stream events."""
        import app.backend.main as backend_main
        from app.backend import ui_routes

        original_client = backend_main.client
        request_id = "chat-event-request"

        class _MockClient:
            async def send_message(
                self,
                message: str,
                request_id: str | None = None,
                metadata: dict[str, object] | None = None,
                include_reasoning: bool | None = None,
            ):
                del message, request_id, metadata, include_reasoning
                from app.backend.models import AgentResponse

                return AgentResponse(
                    response="Hello from agent", reasoning=None, reasoning_tokens=None
                )

        queue = asyncio.Queue()
        ui_routes._ui_stream_subscribers.add(queue)
        backend_main.client = _MockClient()
        try:
            response = client.post(
                "/api/ui/responses",
                json={"model": "progenitor-v1", "input": "Hello!", "stream": False},
                headers={"X-Request-ID": request_id},
            )
            events = [queue.get_nowait()["envelope"] for _ in range(queue.qsize())]
        finally:
            ui_routes._ui_stream_subscribers.discard(queue)
            backend_main.client = original_client

        assert response.status_code == 202
        assert response.json() == {
            "request_id": request_id,
            "event_types": ["chat.response.started"],
        }
        assert [event.type for event in events] == [
            "chat.response.started",
            "chat.response.delta",
            "chat.response.completed",
        ]
        assert all(event.payload["request_id"] == request_id for event in events)
        assert events[1].payload["delta"] == "Hello from agent"

    def test_response_request_preserves_nonempty_message_content(self):
        contents = [
            "Line 1\nLine 2\nLine 3",
            "Hello 世界! 🌍 Привет мир! مرحبا",
            '<script>alert("xss")</script> `code` **bold**',
            '{"key": "value", "array": [1, 2, 3]}',
        ]
        for content in contents:
            request = ResponseRequest(model="progenitor-v1", input=content, stream=False)
            assert request.input == content


class TestStreamingEvents:
    """Tests for streaming event handling."""

    def test_streaming_response_request_validation(self):
        """Test StreamingResponseRequest validation."""
        # Valid string input
        valid_str = ResponseRequest(model="test", input="Hello", stream=True)
        StreamingResponseRequest.validate_for_streaming(valid_str)

        # Empty string input
        empty_str = ResponseRequest(model="test", input="   ", stream=True)
        with pytest.raises(ValueError, match="empty"):
            StreamingResponseRequest.validate_for_streaming(empty_str)

        # Valid list input
        valid_list = ResponseRequest(
            model="test",
            input=[ResponseInputMessage(role="user", content="Hello")],
            stream=True,
        )
        StreamingResponseRequest.validate_for_streaming(valid_list)

        # Empty list input
        empty_list = ResponseRequest(model="test", input=[], stream=True)
        with pytest.raises(ValueError, match="empty"):
            StreamingResponseRequest.validate_for_streaming(empty_list)


class TestResponsesAPIEvents:
    """Responses input normalization remains valid before event dispatch."""

    def test_responses_api_accepts_string_and_message_list_inputs(self):
        string_input = ResponseRequest(model="progenitor-v1", input="Hello, world!")
        list_input = ResponseRequest(
            model="progenitor-v1",
            input=[ResponseInputMessage(role="user", content="Hello!")],
        )

        assert string_input.input == "Hello, world!"
        assert list_input.input[0].content == "Hello!"


class TestSSEEventDelivery:
    """Tests for Server-Sent Events delivery."""

    def test_broadcast_chat_event_functionality(self):
        """Test broadcast_ui_event delivers chat-scoped events to stream subscribers."""
        from app.backend.ui_routes import _ui_stream_subscribers, broadcast_ui_event

        _ui_stream_subscribers.clear()

        chat_queue = asyncio.Queue()
        _ui_stream_subscribers.add(chat_queue)

        try:
            broadcast_ui_event(
                "agent_message",
                {"content": "Hello from agent"},
                scope="chat",
                request_id="agent-message-request",
            )

            assert chat_queue.qsize() == 1

            event = chat_queue.get_nowait()
            assert event["type"] == "ui.event"
            envelope = event["envelope"]
            assert envelope.type == "agent_message"
            assert envelope.scope == "chat"
            assert envelope.payload["content"] == "Hello from agent"
            assert envelope.payload["request_id"] == "agent-message-request"
        finally:
            _ui_stream_subscribers.discard(chat_queue)

    def test_broadcast_status_event_functionality(self):
        """Test broadcast_ui_event delivers status-typed events to stream subscribers."""
        from app.backend.ui_routes import _ui_stream_subscribers, broadcast_ui_event

        _ui_stream_subscribers.clear()

        queue = asyncio.Queue()
        _ui_stream_subscribers.add(queue)

        try:
            broadcast_ui_event(
                "status.updated",
                {"uptime": 100},
                request_id="status-update-request",
            )

            assert not queue.empty()
            event = queue.get_nowait()
            assert event["type"] == "ui.event"
            envelope = event["envelope"]
            assert envelope.type == "status.updated"
            assert envelope.payload["uptime"] == 100
            assert envelope.payload["request_id"] == "status-update-request"
        finally:
            _ui_stream_subscribers.discard(queue)


class TestEventPayloadValidation:
    """Finite success responses contain only the acknowledgement contract."""

    def test_acknowledged_response_has_no_legacy_response_payload(self):
        from app.backend.chat_sessions import UiRequestAcknowledgement

        acknowledgement = UiRequestAcknowledgement(
            request_id="response-ack-request",
            event_types=["chat.response.started"],
        )
        assert acknowledgement.model_dump() == {
            "request_id": "response-ack-request",
            "event_types": ["chat.response.started"],
        }


class TestErrorEvents:
    """Tests for error event handling."""

    def test_invalid_json_returns_error(self, client):
        """Test invalid JSON returns proper error."""
        response = client.post(
            "/api/ui/responses",
            content="not valid json",
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 422

    def test_missing_required_fields_error(self, client):
        """Test missing required fields returns error."""
        response = client.post(
            "/api/ui/responses",
            json={"model": "test"},  # Missing input
        )
        assert response.status_code == 422

    def test_invalid_model_handled(self, client):
        """Test invalid model name is handled."""
        response = client.post(
            "/api/ui/responses",
            json={
                "model": "nonexistent-model",
                "input": "Test",
            },
        )
        # Should accept any model name but may fail on processing
        assert response.status_code in [202, 400, 500, 503]

    def test_rate_limit_error_format(self, client):
        """Test rate limit error has correct format."""
        # This tests the error response structure, not actual rate limiting
        from app.backend.models import ErrorDetail, ErrorResponse

        error = ErrorResponse(
            error=ErrorDetail(
                message="Rate limit exceeded",
                type="rate_limit_exceeded",
            )
        )
        data = error.model_dump()

        assert "error" in data
        assert "message" in data["error"]
        assert "type" in data["error"]


class TestConnectionEvents:
    """Tests for connection/disconnection event handling."""

    def test_ready_endpoint_connection_check(self, client):
        """Test readiness endpoint returns 404 (deleted)."""
        response = client.get("/ready")
        assert response.status_code == 404

    def test_live_endpoint_connection_check(self, client):
        """Test liveness endpoint returns 404 (deleted)."""
        response = client.get("/live")
        assert response.status_code == 404


class TestResponsePayloadModel:
    """Tests for Responses payload model."""

    def test_payload_creation(self):
        """Test ResponsePayload creation."""
        payload = ResponsePayload(
            id="resp-123",
            model="test-model",
            output=[
                ResponseOutputMessage(
                    content=[
                        ResponseOutputReasoning(
                            summary=[
                                ResponseReasoningSummaryText(
                                    text="Checked constraints before answering."
                                )
                            ]
                        ),
                        ResponseOutputText(text="Hello"),
                    ]
                )
            ],
        )
        assert payload.id == "resp-123"
        assert payload.object == "response"
        assert payload.model == "test-model"
        assert payload.output[0].content[0].type == "reasoning"
        assert payload.output[0].content[1].text == "Hello"


class TestTypingIndicatorEvents:
    """Tests for typing indicator events."""

    def test_typing_indicator_state_transitions(self):
        """Test typing indicator event state transitions."""
        from app.backend.ui_routes import _ui_stream_subscribers, broadcast_ui_event

        _ui_stream_subscribers.clear()

        queue = asyncio.Queue()
        _ui_stream_subscribers.add(queue)

        try:
            # Simulate typing indicator sequence
            broadcast_ui_event("typing_start", {"agent": "progenitor"}, scope="chat")
            broadcast_ui_event(
                "typing_update", {"agent": "progenitor", "text": "..."}, scope="chat"
            )
            broadcast_ui_event("typing_stop", {"agent": "progenitor"}, scope="chat")

            events = []
            while not queue.empty():
                events.append(queue.get_nowait())

            assert len(events) == 3
            assert events[0]["envelope"].type == "typing_start"
            assert events[1]["envelope"].type == "typing_update"
            assert events[2]["envelope"].type == "typing_stop"
        finally:
            _ui_stream_subscribers.discard(queue)


class TestMessageContentTypes:
    """Structured input accepts rich user text before stream dispatch."""

    def test_rich_message_content_is_preserved(self):
        contents = [
            "```python\ndef hello():\n    print('Hello')\n```",
            "# Heading\n\n- Item 1\n\n**Bold** and *italic*",
            "Check out https://github.com/example/repo",
            "| Col1 | Col2 |\n|------|------|\n| A | B |",
        ]
        for content in contents:
            assert ResponseRequest(model="progenitor-v1", input=content).input == content
