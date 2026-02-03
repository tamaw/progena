"""
Tests for BL-007: BuildSettings Schema and Build Configuration

Tests cover:
1. BuildSettings model creation and factory method
2. Status transitions (pending, in_progress, completed, failed, error, suspended)
3. Directory derivation from type + project_name
4. Submodule tracking
"""

import pytest
from datetime import datetime

from app.progenitor.state.execution_schemas import (
    BuildSettings,
    BuildType,
    BuildStatus,
)


class TestBuildType:
    """Test BuildType enum."""

    def test_agent_type(self):
        assert BuildType.AGENT.value == "agent"

    def test_mcp_type(self):
        assert BuildType.MCP.value == "mcp"

    def test_from_string(self):
        assert BuildType("agent") == BuildType.AGENT
        assert BuildType("mcp") == BuildType.MCP


class TestBuildStatus:
    """Test BuildStatus enum."""

    def test_all_statuses_exist(self):
        expected = {"pending", "in_progress", "completed", "failed", "suspended", "error"}
        actual = {s.value for s in BuildStatus}
        assert actual == expected

    def test_from_string(self):
        assert BuildStatus("pending") == BuildStatus.PENDING
        assert BuildStatus("in_progress") == BuildStatus.IN_PROGRESS
        assert BuildStatus("completed") == BuildStatus.COMPLETED
        assert BuildStatus("failed") == BuildStatus.FAILED
        assert BuildStatus("suspended") == BuildStatus.SUSPENDED
        assert BuildStatus("error") == BuildStatus.ERROR


class TestBuildSettingsCreation:
    """Test BuildSettings model creation."""

    def test_create_from_llm_output_agent(self):
        """Test creating agent build settings."""
        settings = BuildSettings.create_from_llm_output(
            build_id="build_abc123",
            project_name="weather_agent",
            build_type="agent",
            user_request="build a weather agent",
            description="Weather lookup agent",
        )

        assert settings.build_id == "build_abc123"
        assert settings.project_name == "weather_agent"
        assert settings.build_type == "agent"  # Enum value serialized
        assert settings.project_directory == "app/agents/weather_agent"
        assert settings.allowable_directories == ["app/agents/weather_agent"]
        assert settings.status == "pending"
        assert settings.user_request == "build a weather agent"
        assert settings.description == "Weather lookup agent"

    def test_create_from_llm_output_mcp(self):
        """Test creating MCP build settings."""
        settings = BuildSettings.create_from_llm_output(
            build_id="build_xyz789",
            project_name="database_mcp",
            build_type="mcp",
            user_request="create an MCP server for database access",
        )

        assert settings.build_id == "build_xyz789"
        assert settings.project_name == "database_mcp"
        assert settings.build_type == "mcp"
        assert settings.project_directory == "app/mcp/database_mcp"
        assert settings.allowable_directories == ["app/mcp/database_mcp"]

    def test_project_name_sanitization(self):
        """Test that project names are sanitized for filesystem."""
        settings = BuildSettings.create_from_llm_output(
            build_id="build_test",
            project_name="My Weather-Agent",
            build_type="agent",
            user_request="test",
        )

        # Should be lowercased and use underscores
        assert settings.project_name == "my_weather_agent"
        assert settings.project_directory == "app/agents/my_weather_agent"

    def test_build_type_enum_input(self):
        """Test that BuildType enum can be passed directly."""
        settings = BuildSettings.create_from_llm_output(
            build_id="build_test",
            project_name="test",
            build_type=BuildType.AGENT,
            user_request="test",
        )

        assert settings.build_type == "agent"


class TestBuildSettingsStatusTransitions:
    """Test BuildSettings status transitions."""

    def test_initial_status_is_pending(self):
        settings = BuildSettings.create_from_llm_output(
            build_id="test",
            project_name="test",
            build_type="agent",
            user_request="test",
        )
        assert settings.status == "pending"

    def test_mark_in_progress(self):
        settings = BuildSettings.create_from_llm_output(
            build_id="test",
            project_name="test",
            build_type="agent",
            user_request="test",
        )

        result = settings.mark_in_progress()

        assert settings.status == "in_progress"
        assert result is settings  # Returns self for chaining

    def test_mark_completed(self):
        settings = BuildSettings.create_from_llm_output(
            build_id="test",
            project_name="test",
            build_type="agent",
            user_request="test",
        )

        settings.mark_in_progress()
        settings.mark_completed()

        assert settings.status == "completed"
        assert settings.completed_at is not None

    def test_mark_failed_with_error(self):
        settings = BuildSettings.create_from_llm_output(
            build_id="test",
            project_name="test",
            build_type="agent",
            user_request="test",
        )

        settings.mark_failed("Build validation failed")

        assert settings.status == "failed"
        assert settings.error_message == "Build validation failed"
        assert settings.completed_at is not None

    def test_mark_error_with_message(self):
        settings = BuildSettings.create_from_llm_output(
            build_id="test",
            project_name="test",
            build_type="agent",
            user_request="test",
        )

        settings.mark_error("Unrecoverable LLM error")

        assert settings.status == "error"
        assert settings.error_message == "Unrecoverable LLM error"
        assert settings.completed_at is not None

    def test_mark_suspended(self):
        settings = BuildSettings.create_from_llm_output(
            build_id="test",
            project_name="test",
            build_type="agent",
            user_request="test",
        )

        settings.mark_in_progress()
        settings.mark_suspended("User cancelled")

        assert settings.status == "suspended"
        assert settings.error_message == "User cancelled"
        assert settings.completed_at is None  # Suspended != completed


class TestBuildSettingsSubmodule:
    """Test BuildSettings submodule tracking."""

    def test_initial_submodule_state(self):
        settings = BuildSettings.create_from_llm_output(
            build_id="test",
            project_name="test",
            build_type="agent",
            user_request="test",
        )

        assert settings.submodule_path is None
        assert settings.submodule_initialized is False

    def test_set_submodule(self):
        settings = BuildSettings.create_from_llm_output(
            build_id="test",
            project_name="test_agent",
            build_type="agent",
            user_request="test",
        )

        settings.set_submodule("app/agents/test_agent")

        assert settings.submodule_path == "app/agents/test_agent"
        assert settings.submodule_initialized is True


class TestBuildSettingsSerialization:
    """Test BuildSettings serialization for state storage."""

    def test_model_dump(self):
        settings = BuildSettings.create_from_llm_output(
            build_id="build_123",
            project_name="weather",
            build_type="agent",
            user_request="build weather agent",
            description="Weather service",
        )

        data = settings.model_dump()

        assert isinstance(data, dict)
        assert data["build_id"] == "build_123"
        assert data["project_name"] == "weather"
        assert data["build_type"] == "agent"
        assert data["project_directory"] == "app/agents/weather"
        assert data["status"] == "pending"

    def test_round_trip_serialization(self):
        """Test that settings can be serialized and deserialized."""
        original = BuildSettings.create_from_llm_output(
            build_id="build_test",
            project_name="test_agent",
            build_type="agent",
            user_request="test",
        )
        original.mark_in_progress()
        original.set_submodule("app/agents/test_agent")

        data = original.model_dump()
        restored = BuildSettings(**data)

        assert restored.build_id == original.build_id
        assert restored.project_name == original.project_name
        assert restored.build_type == original.build_type
        assert restored.status == original.status
        assert restored.submodule_path == original.submodule_path
        assert restored.submodule_initialized == original.submodule_initialized
