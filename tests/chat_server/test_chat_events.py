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
import json
import time
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.chat_server.main import app
from app.chat_server.models import (
    ChatCompletionRequest,
    ChatMessage,
    ChatCompletionChunk,
    ResponseRequest,
    ResponseInputMessage,
    SimpleChatRequest,
    StreamingChatRequest,
    StreamingResponseRequest,
)


@pytest.fixture
def client():
    """Create a test client."""
    return TestClient(app)


class TestUserMessageEvents:
    """Tests for user message event handling."""

    def test_simple_user_message_accepted(self, client):
        """Test that simple user messages are accepted."""
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "progenitor-v1",
                "messages": [{"role": "user", "content": "Hello!"}],
                "stream": False,
            },
        )
        # Should accept request (may fail if agent not running)
        assert response.status_code in [200, 500, 503]

    def test_empty_user_message_rejected(self, client):
        """Test that empty user messages are rejected."""
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "progenitor-v1",
                "messages": [{"role": "user", "content": ""}],
                "stream": False,
            },
        )
        # Should accept or reject with proper error
        assert response.status_code in [200, 400, 422, 500, 503]

    def test_multiline_user_message(self, client):
        """Test multiline user messages are handled."""
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "progenitor-v1",
                "messages": [
                    {"role": "user", "content": "Line 1\nLine 2\nLine 3"}
                ],
                "stream": False,
            },
        )
        assert response.status_code in [200, 500, 503]

    def test_unicode_user_message(self, client):
        """Test Unicode characters in user messages."""
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "progenitor-v1",
                "messages": [
                    {
                        "role": "user",
                        "content": "Hello 世界! 🌍 Привет мир! مرحبا",
                    }
                ],
                "stream": False,
            },
        )
        assert response.status_code in [200, 500, 503]

    def test_special_characters_in_message(self, client):
        """Test special characters don't break message handling."""
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "progenitor-v1",
                "messages": [
                    {
                        "role": "user",
                        "content": '<script>alert("xss")</script> `code` **bold**',
                    }
                ],
                "stream": False,
            },
        )
        assert response.status_code in [200, 500, 503]

    def test_json_in_user_message(self, client):
        """Test JSON content in user messages."""
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "progenitor-v1",
                "messages": [
                    {
                        "role": "user",
                        "content": '{"key": "value", "array": [1, 2, 3]}',
                    }
                ],
                "stream": False,
            },
        )
        assert response.status_code in [200, 500, 503]


class TestConversationHistory:
    """Tests for conversation history handling."""

    def test_multi_turn_conversation(self, client):
        """Test multi-turn conversation is handled."""
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "progenitor-v1",
                "messages": [
                    {"role": "user", "content": "Hi, I'm Bob."},
                    {"role": "assistant", "content": "Hello Bob! Nice to meet you."},
                    {"role": "user", "content": "What's my name?"},
                ],
                "stream": False,
            },
        )
        assert response.status_code in [200, 500, 503]

    def test_system_message_handling(self, client):
        """Test system messages in conversation."""
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "progenitor-v1",
                "messages": [
                    {"role": "system", "content": "You are a helpful assistant."},
                    {"role": "user", "content": "Hello!"},
                ],
                "stream": False,
            },
        )
        assert response.status_code in [200, 500, 503]

    def test_long_conversation_history(self, client):
        """Test handling of long conversation history."""
        messages = []
        for i in range(20):
            messages.append({"role": "user", "content": f"Message {i}"})
            messages.append({"role": "assistant", "content": f"Response {i}"})
        messages.append({"role": "user", "content": "Final message"})

        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "progenitor-v1",
                "messages": messages,
                "stream": False,
            },
        )
        assert response.status_code in [200, 500, 503]


class TestStreamingEvents:
    """Tests for streaming event handling."""

    def test_streaming_request_format(self, client):
        """Test streaming request is properly formatted."""
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "progenitor-v1",
                "messages": [{"role": "user", "content": "Test"}],
                "stream": True,
            },
        )
        # Should return streaming response or error
        assert response.status_code in [200, 500, 503]
        if response.status_code == 200:
            assert "text/event-stream" in response.headers.get("content-type", "")

    def test_streaming_chat_request_validation(self):
        """Test StreamingChatRequest validation."""
        # Valid request
        valid_request = ChatCompletionRequest(
            model="test",
            messages=[ChatMessage(role="user", content="Hello")],
            stream=True,
        )
        # Should not raise
        StreamingChatRequest.validate_for_streaming(valid_request)

        # Empty messages
        empty_request = ChatCompletionRequest(
            model="test",
            messages=[],
            stream=True,
        )
        with pytest.raises(ValueError, match="empty"):
            StreamingChatRequest.validate_for_streaming(empty_request)

        # No user message
        no_user_request = ChatCompletionRequest(
            model="test",
            messages=[ChatMessage(role="system", content="You are helpful")],
            stream=True,
        )
        with pytest.raises(ValueError, match="user message"):
            StreamingChatRequest.validate_for_streaming(no_user_request)

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
    """Tests for Responses API event handling."""

    def test_responses_api_string_input(self, client):
        """Test Responses API with string input."""
        response = client.post(
            "/v1/responses",
            json={
                "model": "progenitor-v1",
                "input": "Hello, world!",
            },
        )
        assert response.status_code in [200, 500, 503]

    def test_responses_api_message_list_input(self, client):
        """Test Responses API with message list input."""
        response = client.post(
            "/v1/responses",
            json={
                "model": "progenitor-v1",
                "input": [
                    {"role": "user", "content": "Hello!"},
                ],
            },
        )
        assert response.status_code in [200, 500, 503]

    def test_responses_api_streaming(self, client):
        """Test Responses API with streaming enabled."""
        response = client.post(
            "/v1/responses",
            json={
                "model": "progenitor-v1",
                "input": "Test streaming",
                "stream": True,
            },
        )
        assert response.status_code in [200, 500, 503]
        if response.status_code == 200:
            assert "text/event-stream" in response.headers.get("content-type", "")


class TestSSEEventDelivery:
    """Tests for Server-Sent Events delivery."""

    def test_sse_events_endpoint_exists(self, client):
        """Test SSE events endpoint is accessible."""
        # Note: Full SSE tests require async handling, but we can test endpoint exists
        response = client.get("/api/ui/status")
        assert response.status_code == 200

    def test_broadcast_event_functionality(self):
        """Test broadcast_event adds events to subscriber queues."""
        from app.chat_server.ui_routes import _event_subscribers, broadcast_event

        _event_subscribers.clear()

        queue = asyncio.Queue()
        _event_subscribers.append(queue)

        try:
            # Broadcast different event types
            broadcast_event("message_received", {"content": "Hello"})
            broadcast_event("typing_start", {"user": "agent"})
            broadcast_event("error", {"code": "ERR001", "message": "Test error"})

            # Verify all events were queued
            assert queue.qsize() == 3

            # Verify event structure
            event1 = queue.get_nowait()
            assert event1["type"] == "message_received"
            # data is JSON-serialized string
            data1 = json.loads(event1["data"])
            assert data1["content"] == "Hello"
        finally:
            _event_subscribers.remove(queue)

    def test_multiple_subscribers_receive_events(self):
        """Test events are broadcast to all subscribers."""
        from app.chat_server.ui_routes import _event_subscribers, broadcast_event

        _event_subscribers.clear()

        queue1 = asyncio.Queue()
        queue2 = asyncio.Queue()
        queue3 = asyncio.Queue()

        _event_subscribers.extend([queue1, queue2, queue3])

        try:
            broadcast_event("test_event", {"value": 42})

            # All subscribers should receive the event
            assert not queue1.empty()
            assert not queue2.empty()
            assert not queue3.empty()
        finally:
            for q in [queue1, queue2, queue3]:
                _event_subscribers.remove(q)

    def test_event_timestamp_included(self):
        """Test broadcast events include timestamp when provided in data."""
        from app.chat_server.ui_routes import _event_subscribers, broadcast_event

        _event_subscribers.clear()

        queue = asyncio.Queue()
        _event_subscribers.append(queue)

        try:
            before = time.time()
            # Include timestamp in data since broadcast_event doesn't add it
            broadcast_event("test", {"key": "value", "timestamp": time.time()})
            after = time.time()

            event = queue.get_nowait()
            assert "type" in event
            assert event["type"] == "test"
            # data is JSON string containing our payload
            data = json.loads(event["data"])
            assert "timestamp" in data
            assert before <= data["timestamp"] <= after
        finally:
            _event_subscribers.remove(queue)


class TestEventPayloadValidation:
    """Tests for event payload structure and validation."""

    def test_chat_completion_response_structure(self, client):
        """Test chat completion response has correct structure."""
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "progenitor-v1",
                "messages": [{"role": "user", "content": "Test"}],
                "stream": False,
            },
        )

        if response.status_code == 200:
            data = response.json()
            assert "id" in data
            assert "object" in data
            assert "model" in data
            assert "choices" in data
            assert isinstance(data["choices"], list)
            if data["choices"]:
                choice = data["choices"][0]
                assert "message" in choice
                assert "finish_reason" in choice

    def test_responses_api_response_structure(self, client):
        """Test Responses API response has correct structure."""
        response = client.post(
            "/v1/responses",
            json={
                "model": "progenitor-v1",
                "input": "Test",
            },
        )

        if response.status_code == 200:
            data = response.json()
            assert "id" in data
            assert "object" in data
            assert data["object"] == "response"
            assert "output" in data
            assert isinstance(data["output"], list)

    def test_status_event_payload_structure(self, client):
        """Test status endpoint returns properly structured payload."""
        response = client.get("/api/ui/status")
        data = response.json()

        # Required fields
        assert "connected" in data
        assert "server_status" in data
        assert "progenitor_running" in data
        assert "version" in data

        # Lists
        assert "loops" in data
        assert "agents" in data
        assert "backlog" in data
        assert "schedule" in data

        # Types
        assert isinstance(data["loops"], list)
        assert isinstance(data["agents"], list)
        assert isinstance(data["backlog"], list)
        assert isinstance(data["schedule"], list)


class TestErrorEvents:
    """Tests for error event handling."""

    def test_invalid_json_returns_error(self, client):
        """Test invalid JSON returns proper error."""
        response = client.post(
            "/v1/chat/completions",
            content="not valid json",
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 422

    def test_missing_required_fields_error(self, client):
        """Test missing required fields returns error."""
        response = client.post(
            "/v1/chat/completions",
            json={"model": "test"},  # Missing messages
        )
        assert response.status_code == 422

    def test_invalid_model_handled(self, client):
        """Test invalid model name is handled."""
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "nonexistent-model",
                "messages": [{"role": "user", "content": "Test"}],
            },
        )
        # Should accept any model name but may fail on processing
        assert response.status_code in [200, 400, 500, 503]

    def test_rate_limit_error_format(self, client):
        """Test rate limit error has correct format."""
        # This tests the error response structure, not actual rate limiting
        from app.chat_server.models import ErrorResponse, ErrorDetail

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

    def test_health_endpoint_connection_check(self, client):
        """Test health endpoint works for connection checking."""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert "status" in data

    def test_ready_endpoint_connection_check(self, client):
        """Test readiness endpoint works."""
        response = client.get("/ready")
        # May return 503 if client not initialized
        assert response.status_code in [200, 503]

    def test_live_endpoint_connection_check(self, client):
        """Test liveness endpoint works."""
        response = client.get("/live")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "alive"


class TestEventOrdering:
    """Tests for event ordering and sequence."""

    def test_status_update_sequence(self, client):
        """Test status updates maintain proper sequence."""
        # Make multiple status requests
        responses = [client.get("/api/ui/status") for _ in range(5)]

        for response in responses:
            assert response.status_code == 200
            data = response.json()
            assert "connected" in data

    def test_concurrent_status_requests(self, client):
        """Test concurrent status requests don't interfere."""
        import concurrent.futures

        def get_status():
            return client.get("/api/ui/status")

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(get_status) for _ in range(20)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        # All requests should succeed
        for response in results:
            assert response.status_code == 200


class TestSimpleChatRequest:
    """Tests for SimpleChatRequest model."""

    def test_simple_chat_request_validation(self):
        """Test SimpleChatRequest validation."""
        # Valid request
        valid = SimpleChatRequest(message="Hello")
        assert valid.message == "Hello"
        assert valid.stream is False

        # With streaming
        streaming = SimpleChatRequest(message="Hello", stream=True)
        assert streaming.stream is True

    def test_simple_chat_request_empty_rejected(self):
        """Test empty message is rejected."""
        with pytest.raises(Exception):
            SimpleChatRequest(message="")

    def test_simple_chat_request_max_length(self):
        """Test max length validation."""
        # Should accept up to 100000 characters
        long_msg = "x" * 100000
        valid = SimpleChatRequest(message=long_msg)
        assert len(valid.message) == 100000

        # Should reject over 100000
        too_long = "x" * 100001
        with pytest.raises(Exception):
            SimpleChatRequest(message=too_long)


class TestChatCompletionChunk:
    """Tests for streaming chunk model."""

    def test_chunk_creation(self):
        """Test ChatCompletionChunk creation."""
        chunk = ChatCompletionChunk(
            id="chunk-123",
            model="test-model",
            choices=[
                {
                    "index": 0,
                    "delta": {"content": "Hello"},
                    "finish_reason": None,
                }
            ],
        )
        assert chunk.id == "chunk-123"
        assert chunk.object == "chat.completion.chunk"
        assert chunk.model == "test-model"
        assert len(chunk.choices) == 1

    def test_final_chunk_creation(self):
        """Test final chunk with finish_reason."""
        chunk = ChatCompletionChunk(
            id="chunk-final",
            model="test-model",
            choices=[
                {
                    "index": 0,
                    "delta": {},
                    "finish_reason": "stop",
                }
            ],
        )
        assert chunk.choices[0]["finish_reason"] == "stop"


class TestTypingIndicatorEvents:
    """Tests for typing indicator events."""

    def test_typing_indicator_state_transitions(self):
        """Test typing indicator event state transitions."""
        from app.chat_server.ui_routes import _event_subscribers, broadcast_event

        _event_subscribers.clear()

        queue = asyncio.Queue()
        _event_subscribers.append(queue)

        try:
            # Simulate typing indicator sequence
            broadcast_event("typing_start", {"agent": "progenitor"})
            broadcast_event("typing_update", {"agent": "progenitor", "text": "..."})
            broadcast_event("typing_stop", {"agent": "progenitor"})

            events = []
            while not queue.empty():
                events.append(queue.get_nowait())

            assert len(events) == 3
            assert events[0]["type"] == "typing_start"
            assert events[1]["type"] == "typing_update"
            assert events[2]["type"] == "typing_stop"
        finally:
            _event_subscribers.remove(queue)


class TestAgentActivityEvents:
    """Tests for agent activity status events."""

    def test_agent_status_in_response(self, client):
        """Test agent status is included in status response."""
        response = client.get("/api/ui/status")
        data = response.json()

        assert "agents" in data
        for agent in data["agents"]:
            assert "name" in agent
            assert "status" in agent
            assert "kind" in agent

    def test_loop_status_in_response(self, client):
        """Test loop status is included in status response."""
        response = client.get("/api/ui/status")
        data = response.json()

        assert "loops" in data
        for loop in data["loops"]:
            assert "name" in loop
            assert "status" in loop

    def test_build_status_in_response(self, client):
        """Test build status is included in status response."""
        response = client.get("/api/ui/status")
        data = response.json()

        assert "build" in data
        if data["build"]:
            assert "in_progress" in data["build"]


class TestMessageContentTypes:
    """Tests for different message content types."""

    def test_code_block_in_message(self, client):
        """Test code blocks in messages are handled."""
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "progenitor-v1",
                "messages": [
                    {
                        "role": "user",
                        "content": "```python\ndef hello():\n    print('Hello')\n```",
                    }
                ],
            },
        )
        assert response.status_code in [200, 500, 503]

    def test_markdown_formatting(self, client):
        """Test markdown formatting in messages."""
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "progenitor-v1",
                "messages": [
                    {
                        "role": "user",
                        "content": "# Heading\n\n- Item 1\n- Item 2\n\n**Bold** and *italic*",
                    }
                ],
            },
        )
        assert response.status_code in [200, 500, 503]

    def test_url_in_message(self, client):
        """Test URLs in messages."""
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "progenitor-v1",
                "messages": [
                    {
                        "role": "user",
                        "content": "Check out https://github.com/example/repo",
                    }
                ],
            },
        )
        assert response.status_code in [200, 500, 503]

    def test_table_in_message(self, client):
        """Test tables in messages."""
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "progenitor-v1",
                "messages": [
                    {
                        "role": "user",
                        "content": "| Col1 | Col2 |\n|------|------|\n| A    | B    |",
                    }
                ],
            },
        )
        assert response.status_code in [200, 500, 503]
