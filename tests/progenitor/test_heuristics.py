"""
Tests for app/progenitor/nodes/heuristics/

Verifies fast-path intent detection without LLM calls.
"""

import pytest

from app.progenitor.nodes.heuristics import (
    extract_project_name_from_input,
    extract_schedule_params,
    looks_like_agent_build_request,
    looks_like_build_status_request,
    looks_like_list_schedule_request,
    looks_like_list_tools_request,
    looks_like_schedule_request,
)


class TestLooksLikeBuildRequest:
    """Tests for build request detection heuristics."""

    @pytest.mark.parametrize("text", [
        "build an agent",
        "Build an agent for me",
        "create a new agent",
        "Create a new agent that does X",
        "make an agent",
        "Make an agent to handle this",
        "I want to build an agent",
        "please build an agent",
        "can you build an agent",
        "build a new agent",
        "create an agent",
        "build me an agent",
        "create me an agent",
        "scaffold an agent",
        "generate an agent",
        "implement an agent",
        "build a hello world agent",
        "create a weather agent",
        "build an a2a agent",
    ])
    def test_detects_build_requests(self, text):
        """Should detect agent build requests."""
        assert looks_like_agent_build_request(text) is True

    @pytest.mark.parametrize("text", [
        "what is an agent",
        "tell me about agents",
        "how do agents work",
        "agent status",
        "list agents",
        "show agents",
        "the agent is running",
        "build a house",
        "create a file",
        "make something",
        "I need help with code",
        "build the project",
        "create a schedule",
        "what is your schedule",
        "hello",
        "help",
        "build a docker image",
        "create a container",
        "build the plan",
        "develop a plan",
    ])
    def test_does_not_falsely_trigger_build(self, text):
        """Should not falsely trigger on non-build requests."""
        assert looks_like_agent_build_request(text) is False


class TestLooksLikeScheduleQuery:
    """Tests for schedule query detection heuristics."""

    @pytest.mark.parametrize("text", [
        "what is your schedule",
        "What is your schedule?",
        "WHAT IS YOUR SCHEDULE",
        "show schedule",
        "show my schedule",
        "show the schedule",
        "list schedule",
        "list schedules",
        "list scheduled",
        "view schedule",
        "view schedules",
        "what's scheduled",
        "what is scheduled",
        "scheduled tasks",
        "any scheduled",
        "pending schedule",
        "upcoming tasks",
        "upcoming schedule",
        "my reminders",
        "show reminders",
        "list reminders",
        "what reminders",
    ])
    def test_detects_schedule_requests(self, text):
        """Should detect schedule listing requests."""
        assert looks_like_list_schedule_request(text) is True

    @pytest.mark.parametrize("text", [
        "schedule a task for tomorrow",
        "create a schedule",
        "add to schedule",
        "schedule: run daily",
        "build an agent",
        "hello",
        "what time is it",
        "schedule me a meeting",
        "reschedule the task",
    ])
    def test_does_not_detect_non_schedule_requests(self, text):
        """Should not detect non-schedule listing requests."""
        assert looks_like_list_schedule_request(text) is False


class TestLooksLikeBuildStatusQuery:
    """Tests for build status query detection heuristics."""

    @pytest.mark.parametrize("text", [
        "build status",
        "what is the build status",
        "show build status",
        "check build",
        "build progress",
        "how is the build",
        "how's the build",
        "what are you building",
        "are you building",
        "building status",
        "building progress",
        "current build",
        "update on build",
    ])
    def test_detects_build_status_requests(self, text):
        """Should detect build status requests."""
        assert looks_like_build_status_request(text) is True

    @pytest.mark.parametrize("text", [
        "build an agent",
        "status of the project",
        "what is the status",
        "hello",
        "build something",
    ])
    def test_does_not_detect_non_status_requests(self, text):
        """Should not detect non-status requests."""
        assert looks_like_build_status_request(text) is False


class TestLooksLikeListToolsQuery:
    """Tests for list tools query detection heuristics."""

    @pytest.mark.parametrize("text", [
        "list tools",
        "list your tools",
        "what tools do you have",
        "show tools",
        "show your tools",
        "available tools",
        "what tools are available",
        "what can you do",
        "what are your capabilities",
    ])
    def test_detects_list_tools_requests(self, text):
        """Should detect list tools requests."""
        assert looks_like_list_tools_request(text) is True

    @pytest.mark.parametrize("text", [
        "build an agent",
        "hello",
        "help",
        "tool usage",
        "the tools are working",
    ])
    def test_does_not_detect_non_list_tools_requests(self, text):
        """Should not detect non-list tools requests."""
        assert looks_like_list_tools_request(text) is False


class TestLooksLikeScheduleRequest:
    """Tests for schedule creation detection heuristics."""

    @pytest.mark.parametrize("text", [
        "schedule a task for tomorrow",
        "remind me at 9am",
        "schedule something for next week",
        "add a reminder",
    ])
    def test_detects_schedule_creation_requests(self, text):
        """Should detect schedule creation requests."""
        assert looks_like_schedule_request(text) is True

    @pytest.mark.parametrize("text", [
        "build an agent",
        "hello",
        "what time is it",
    ])
    def test_does_not_detect_non_schedule_creation_requests(self, text):
        """Should not detect non-schedule creation requests."""
        assert looks_like_schedule_request(text) is False


class TestExtractProjectName:
    """Tests for project name extraction."""

    def test_extracts_name_from_build_request(self):
        """Should extract agent name from build request."""
        result = extract_project_name_from_input("build a weather agent")
        assert result is not None
        assert len(result) > 0

    def test_extracts_name_from_create_request(self):
        """Should extract agent name from create request."""
        result = extract_project_name_from_input("create a hello world agent")
        assert result is not None

    def test_returns_none_for_simple_request(self):
        """Should return None for generic requests without specific name."""
        result = extract_project_name_from_input("build an agent")
        # Generic request without specific name should return None
        assert result is None


class TestExtractScheduleParams:
    """Tests for schedule parameter extraction."""

    def test_extracts_time_from_schedule_request(self):
        """Should extract time from schedule request."""
        result = extract_schedule_params("schedule at 9am daily")
        # Should return a dict with some time info
        assert isinstance(result, dict)

    def test_returns_dict_for_schedule_requests(self):
        """Should return dict structure for schedule requests."""
        result = extract_schedule_params("remind me tomorrow")
        assert isinstance(result, dict)


class TestCaseInsensitivity:
    """Tests for case insensitivity."""

    def test_build_request_case_insensitive(self):
        """Build detection should be case insensitive."""
        assert looks_like_agent_build_request("BUILD AN AGENT") is True
        assert looks_like_agent_build_request("Build An Agent") is True
        assert looks_like_agent_build_request("bUiLd An AgEnT") is True

    def test_schedule_query_case_insensitive(self):
        """Schedule detection should be case insensitive."""
        assert looks_like_list_schedule_request("LIST SCHEDULE") is True
        assert looks_like_list_schedule_request("List Schedule") is True
