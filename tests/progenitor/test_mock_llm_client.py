"""Tests for MockLLMClient.

Tests the mock LLM client for demo mode functionality.
"""

import os
from unittest.mock import patch

import pytest

from app.progenitor.llm.mock_client import (
    MockLLMClient,
    create_llm_client,
    is_mock_mode_enabled,
)


class TestIsMockModeEnabled:
    """Tests for is_mock_mode_enabled function."""

    def test_returns_false_by_default(self):
        """Should return False when env var not set."""
        with patch.dict(os.environ, {}, clear=True):
            # Remove the var if present
            os.environ.pop("PROGENITOR_MOCK_LLM", None)
            assert is_mock_mode_enabled() is False

    def test_returns_true_when_set_to_true(self):
        """Should return True when env var is 'true'."""
        with patch.dict(os.environ, {"PROGENITOR_MOCK_LLM": "true"}):
            assert is_mock_mode_enabled() is True

    def test_returns_true_when_set_to_1(self):
        """Should return True when env var is '1'."""
        with patch.dict(os.environ, {"PROGENITOR_MOCK_LLM": "1"}):
            assert is_mock_mode_enabled() is True

    def test_returns_true_when_set_to_yes(self):
        """Should return True when env var is 'yes'."""
        with patch.dict(os.environ, {"PROGENITOR_MOCK_LLM": "yes"}):
            assert is_mock_mode_enabled() is True

    def test_case_insensitive(self):
        """Should be case insensitive."""
        with patch.dict(os.environ, {"PROGENITOR_MOCK_LLM": "TRUE"}):
            assert is_mock_mode_enabled() is True

    def test_returns_false_for_other_values(self):
        """Should return False for other values."""
        with patch.dict(os.environ, {"PROGENITOR_MOCK_LLM": "false"}):
            assert is_mock_mode_enabled() is False


class TestMockLLMClient:
    """Tests for MockLLMClient class."""

    @pytest.fixture
    def client(self):
        """Create a mock client for testing."""
        return MockLLMClient(
            latency_range=(0.01, 0.02),  # Fast for tests
            stream_speed=1000,
            fail_rate=0.0,
        )

    @pytest.mark.asyncio
    async def test_chat_returns_string(self, client):
        """chat should return a string response."""
        messages = [{"role": "user", "content": "Hello"}]
        response = await client.chat(messages)
        assert isinstance(response, str)
        assert len(response) > 0

    @pytest.mark.asyncio
    async def test_chat_with_greeting(self, client):
        """chat should recognize greetings."""
        messages = [{"role": "user", "content": "Hello!"}]
        response = await client.chat(messages)
        assert "Hello" in response or "hello" in response.lower()

    @pytest.mark.asyncio
    async def test_chat_with_help_request(self, client):
        """chat should respond to help requests."""
        messages = [{"role": "user", "content": "What can you do?"}]
        response = await client.chat(messages)
        assert "help" in response.lower() or "progenitor" in response.lower()

    @pytest.mark.asyncio
    async def test_stream_chat_yields_chars(self, client):
        """stream_chat should yield characters."""
        messages = [{"role": "user", "content": "Hello"}]
        chars = []
        async for char in client.stream_chat(messages):
            chars.append(char)
        assert len(chars) > 0
        # Joining should give same result as chat
        response = "".join(chars)
        assert isinstance(response, str)

    @pytest.mark.asyncio
    async def test_complete_returns_string(self, client):
        """complete should return a string."""
        response = await client.complete("Tell me a joke")
        assert isinstance(response, str)
        assert len(response) > 0

    @pytest.mark.asyncio
    async def test_decide_returns_dict(self, client):
        """decide should return a decision dict."""
        result = await client.decide(
            user_input="Create a new file",
            conversation_history=[],
            tool_context="### System Tools\n- list_tools: List all available tools",
        )
        assert isinstance(result, dict)
        assert "action" in result
        assert "reasoning" in result
        assert result["action"] in ["respond", "execute", "plan"]

    @pytest.mark.asyncio
    async def test_decide_execute_for_create(self, client):
        """decide should return execute for creation requests."""
        result = await client.decide(
            user_input="Create a new Python file",
            conversation_history=[],
            tool_context="",
        )
        assert result["action"] == "execute"

    @pytest.mark.asyncio
    async def test_decide_respond_for_questions(self, client):
        """decide should return respond for questions."""
        result = await client.decide(
            user_input="What is Python?",
            conversation_history=[],
            tool_context="",
        )
        assert result["action"] == "respond"

    @pytest.mark.asyncio
    async def test_plan_returns_list(self, client):
        """plan should return a list of work items."""
        result = await client.plan(
            user_input="Build a web app",
            decision={"action": "plan"},
            current_backlog=[],
        )
        assert isinstance(result, list)
        assert len(result) >= 1
        assert all(isinstance(item, dict) for item in result)
        assert all("title" in item for item in result)

    @pytest.mark.asyncio
    async def test_generate_build_spec(self, client):
        """generate_build_spec should return a spec dict."""
        result = await client.generate_build_spec(
            work_item={"id": "test-item", "title": "Test task"},
            context={},
        )
        assert isinstance(result, dict)
        assert "steps" in result
        assert isinstance(result["steps"], list)

    @pytest.mark.asyncio
    async def test_verify_execution(self, client):
        """verify_execution should return verification result."""
        result = await client.verify_execution(
            work_item={"id": "test-item"},
            execution_result={"output": "success"},
        )
        assert isinstance(result, dict)
        assert "success" in result
        assert isinstance(result["success"], bool)
        assert "score" in result

    @pytest.mark.asyncio
    async def test_generate_response(self, client):
        """generate_response should return a string."""
        result = await client.generate_response(
            user_input="How are you?",
            context={},
        )
        assert isinstance(result, str)
        assert len(result) > 0

    @pytest.mark.asyncio
    async def test_summarize(self, client):
        """summarize should return shortened text."""
        long_text = "This is a very long text. " * 50
        result = await client.summarize(long_text, max_length=100)
        assert isinstance(result, str)
        assert len(result) <= 100 + 20  # Allow some buffer for "... [summarized]"

    @pytest.mark.asyncio
    async def test_summarize_short_text_unchanged(self, client):
        """summarize should return short text unchanged."""
        short_text = "Hello world"
        result = await client.summarize(short_text, max_length=100)
        assert result == short_text

    def test_get_stats(self, client):
        """get_stats should return stats dict."""
        stats = client.get_stats()
        assert stats["mode"] == "mock"
        assert stats["call_count"] == 0

    @pytest.mark.asyncio
    async def test_call_count_increments(self, client):
        """Call count should increment with each call."""
        assert client.get_stats()["call_count"] == 0
        await client.chat([{"role": "user", "content": "Hi"}])
        assert client.get_stats()["call_count"] == 1
        await client.decide("test", [], [])
        assert client.get_stats()["call_count"] == 2

    @pytest.mark.asyncio
    async def test_fail_rate(self):
        """Should fail at specified rate."""
        # 100% fail rate
        client = MockLLMClient(
            latency_range=(0.001, 0.001),
            fail_rate=1.0,
        )
        with pytest.raises(RuntimeError, match="Simulated API failure"):
            await client.chat([{"role": "user", "content": "Hi"}])


class TestCreateLLMClient:
    """Tests for create_llm_client factory function."""

    def test_force_mock_returns_mock_client(self):
        """force_mock=True should return MockLLMClient."""
        client = create_llm_client(force_mock=True)
        assert isinstance(client, MockLLMClient)

    def test_env_var_returns_mock_client(self):
        """Environment variable should trigger mock client."""
        with patch.dict(os.environ, {"PROGENITOR_MOCK_LLM": "true"}):
            client = create_llm_client()
            assert isinstance(client, MockLLMClient)


class TestMockResponseContextuality:
    """Tests for contextual response generation."""

    @pytest.fixture
    def client(self):
        """Create a mock client for testing."""
        return MockLLMClient(latency_range=(0.001, 0.002))

    @pytest.mark.asyncio
    async def test_code_request_includes_code(self, client):
        """Code requests should include code blocks."""
        messages = [{"role": "user", "content": "Write me a function"}]
        response = await client.chat(messages)
        assert "```" in response or "def " in response

    @pytest.mark.asyncio
    async def test_create_request_includes_steps(self, client):
        """Create requests should include steps."""
        messages = [{"role": "user", "content": "Create a new project"}]
        response = await client.chat(messages)
        assert "1." in response or "✓" in response

    @pytest.mark.asyncio
    async def test_default_response_mentions_demo(self, client):
        """Default responses should mention demo mode."""
        messages = [{"role": "user", "content": "xyzabc123randomquery"}]
        response = await client.chat(messages)
        assert "demo" in response.lower() or "mode" in response.lower()


class TestMockClientLatency:
    """Tests for simulated latency behavior."""

    @pytest.mark.asyncio
    async def test_respects_latency_range(self):
        """Should have latency within specified range."""
        import time

        client = MockLLMClient(latency_range=(0.1, 0.15))

        start = time.perf_counter()
        await client.chat([{"role": "user", "content": "Hi"}])
        elapsed = time.perf_counter() - start

        # Should be at least minimum latency
        assert elapsed >= 0.09  # Small buffer for timing

    @pytest.mark.asyncio
    async def test_zero_latency_possible(self):
        """Zero latency should work."""
        client = MockLLMClient(latency_range=(0.0, 0.0))

        # Should complete very quickly
        import time
        start = time.perf_counter()
        await client.chat([{"role": "user", "content": "Hi"}])
        elapsed = time.perf_counter() - start

        assert elapsed < 0.1  # Should be nearly instant
