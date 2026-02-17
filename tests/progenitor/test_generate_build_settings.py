"""
Tests for BL-007: Generate Build Settings

Tests cover:
1. _generate_build_settings extracts project name from user input
2. _generate_build_settings determines build type (agent vs mcp)
3. _generate_build_settings creates proper BuildSettings object
4. Project name extraction patterns
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

# Import the function under test
from app.progenitor.nodes.decide import _extract_history, _generate_build_settings


@pytest.fixture
def mock_llm_client():
    """Create a mock LLM client for testing.

    Note: Use MagicMock as base so synchronous methods like _get_json_response_format
    don't return coroutines. Only explicitly make async methods use AsyncMock.
    """
    client = MagicMock()
    client.complete = AsyncMock(return_value='{"project_name": "extracted_name", "build_type": "agent", "reasoning": "test"}')
    client._get_json_response_format = MagicMock(return_value={"type": "json_object"})
    return client


class TestBuildTypeDetection:
    """Test build type detection from user input."""

    @pytest.mark.asyncio
    async def test_detects_tool_type(self, mock_llm_client):
        """Test that tool is detected from user input."""
        settings = await _generate_build_settings(
            user_input="create an MCP tool for database access",
            llm_client=mock_llm_client,
            llm_action="build_tool",
        )

        assert settings.build_type == "mcp"
        assert settings.project_directory.startswith("build/mcp/")

    @pytest.mark.asyncio
    async def test_detects_agent_type_by_default(self, mock_llm_client):
        """Test that agent is the default build type."""
        settings = await _generate_build_settings(
            user_input="build a weather service",
            llm_client=mock_llm_client,
            llm_action="build_agent",
        )

        assert settings.build_type == "agent"
        assert settings.project_directory.startswith("build/agents/")

    @pytest.mark.asyncio
    async def test_detects_fastmcp_tool_keyword(self, mock_llm_client):
        """Test that 'mcp tool' triggers tool type."""
        settings = await _generate_build_settings(
            user_input="build a fastmcp tool for file operations",
            llm_client=mock_llm_client,
            llm_action="build_tool",
        )

        assert settings.build_type == "mcp"


class TestProjectNameExtraction:
    """Test project name extraction from user input."""

    @pytest.mark.asyncio
    async def test_extracts_name_from_build_pattern(self):
        """Test extracting name from 'build a X agent' pattern."""
        mock_llm = MagicMock()
        mock_llm.complete = AsyncMock(return_value='{"project_name": "weather_agent", "build_type": "agent", "reasoning": "extracted from weather agent request"}')
        mock_llm._get_json_response_format = MagicMock(return_value={"type": "json_object"})

        settings = await _generate_build_settings(
            user_input="build a weather agent",
            llm_client=mock_llm,
            llm_action="build_agent",
        )

        assert "weather" in settings.project_name

    @pytest.mark.asyncio
    async def test_extracts_name_from_create_pattern(self):
        """Test extracting name from 'create a X agent' pattern."""
        mock_llm = MagicMock()
        mock_llm.complete = AsyncMock(return_value='{"project_name": "hello_world", "build_type": "agent", "reasoning": "extracted from hello world agent request"}')
        mock_llm._get_json_response_format = MagicMock(return_value={"type": "json_object"})

        settings = await _generate_build_settings(
            user_input="create a hello world agent",
            llm_client=mock_llm,
            llm_action="build_agent",
        )

        # Should extract "hello world" or similar
        assert "hello" in settings.project_name or "world" in settings.project_name

    @pytest.mark.asyncio
    async def test_extracts_name_with_a2a_keyword(self):
        """Test extracting name when 'a2a' is in the request."""
        mock_llm = MagicMock()
        mock_llm.complete = AsyncMock(return_value='{"project_name": "weather_agent", "build_type": "agent", "reasoning": "extracted from weather a2a agent request"}')
        mock_llm._get_json_response_format = MagicMock(return_value={"type": "json_object"})

        settings = await _generate_build_settings(
            user_input="build a weather a2a agent",
            llm_client=mock_llm,
            llm_action="build_agent",
        )

        assert "weather" in settings.project_name

    @pytest.mark.asyncio
    async def test_sanitizes_project_name(self, mock_llm_client):
        """Test that project names are sanitized."""
        settings = await _generate_build_settings(
            user_input="build a Weather-Data agent",
            llm_client=mock_llm_client,
            llm_action="build_agent",
        )

        # Should be lowercase with underscores
        assert settings.project_name.islower() or settings.project_name.replace("_", "").islower()
        assert "-" not in settings.project_name


class TestBuildSettingsStructure:
    """Test the structure of generated BuildSettings."""

    @pytest.mark.asyncio
    async def test_has_build_id(self, mock_llm_client):
        """Test that BuildSettings has a build_id."""
        settings = await _generate_build_settings(
            user_input="build a test agent",
            llm_client=mock_llm_client,
            llm_action="build_agent",
        )

        assert settings.build_id is not None
        assert settings.build_id.startswith("build_")

    @pytest.mark.asyncio
    async def test_derives_project_directory(self, mock_llm_client):
        """Test that project_directory is derived from type and name."""
        settings = await _generate_build_settings(
            user_input="build a weather agent",
            llm_client=mock_llm_client,
            llm_action="build_agent",
        )

        assert settings.project_directory.startswith("build/agents/")
        assert settings.project_name in settings.project_directory

    @pytest.mark.asyncio
    async def test_sets_user_request(self, mock_llm_client):
        """Test that user_request is stored."""
        user_input = "build a custom weather lookup agent"
        settings = await _generate_build_settings(
            user_input=user_input,
            llm_client=mock_llm_client,
            llm_action="build_agent",
        )

        assert settings.user_request == user_input

    @pytest.mark.asyncio
    async def test_initial_status_is_pending(self, mock_llm_client):
        """Test that initial status is pending."""
        settings = await _generate_build_settings(
            user_input="build an agent",
            llm_client=mock_llm_client,
            llm_action="build_agent",
        )

        assert settings.status == "pending"

    @pytest.mark.asyncio
    async def test_sets_allowable_directories(self, mock_llm_client):
        """Test that allowable_directories restricts to project directory."""
        settings = await _generate_build_settings(
            user_input="build a test agent",
            llm_client=mock_llm_client,
            llm_action="build_agent",
        )

        assert len(settings.allowable_directories) == 1
        assert settings.allowable_directories[0] == settings.project_directory


class TestLLMFallback:
    """Test LLM fallback for name extraction."""

    @pytest.mark.asyncio
    async def test_uses_llm_when_pattern_fails(self):
        """Test that LLM is used when regex patterns don't match."""
        mock_llm = MagicMock()
        mock_llm.complete = AsyncMock(return_value='{"project_name": "weather_stuff", "build_type": "agent", "reasoning": "test"}')
        mock_llm._get_json_response_format = MagicMock(return_value={"type": "json_object"})

        settings = await _generate_build_settings(
            user_input="make something that does weather stuff",  # No clear pattern
            llm_client=mock_llm,
            llm_action="build_agent",
        )

        # Should have a project name (either from LLM or fallback)
        assert settings.project_name is not None
        assert len(settings.project_name) > 0

    @pytest.mark.asyncio
    async def test_fallback_when_llm_fails(self):
        """Test fallback when LLM extraction fails."""
        mock_llm = MagicMock()
        mock_llm.complete = AsyncMock(side_effect=Exception("LLM error"))
        mock_llm._get_json_response_format = MagicMock(return_value={"type": "json_object"})

        settings = await _generate_build_settings(
            user_input="make something",
            llm_client=mock_llm,
            llm_action="build_agent",
        )

        # Should still have a project name from fallback
        assert settings.project_name is not None
        assert len(settings.project_name) > 0

    @pytest.mark.asyncio
    async def test_works_without_llm_client(self):
        """Test that build settings can be generated without LLM client."""
        settings = await _generate_build_settings(
            user_input="build a weather agent",
            llm_client=None,
            llm_action="build_agent",
        )

        assert settings is not None
        assert "weather" in settings.project_name


class TestExtractHistory:
    """Regression tests for conversation history extraction."""

    def test_excludes_latest_matching_user_input(self):
        """Latest intake message should be excluded when passed separately as user_input."""
        messages = [
            {"role": "assistant", "content": "How can I help?"},
            {"role": "user", "content": "build me a weather agent"},
        ]

        history = _extract_history(
            messages,
            limit=10,
            exclude_latest_user_input="build me a weather agent",
        )

        assert history == [{"role": "assistant", "content": "How can I help?"}]

    def test_keeps_non_latest_matching_user_input(self):
        """Earlier matching messages must be preserved; only latest message is skippable."""
        messages = [
            {"role": "user", "content": "same text"},
            {"role": "assistant", "content": "ack"},
            {"role": "user", "content": "same text"},
            {"role": "assistant", "content": "done"},
        ]

        history = _extract_history(
            messages,
            limit=10,
            exclude_latest_user_input="same text",
        )

        assert history == [
            {"role": "user", "content": "same text"},
            {"role": "assistant", "content": "ack"},
            {"role": "user", "content": "same text"},
            {"role": "assistant", "content": "done"},
        ]
