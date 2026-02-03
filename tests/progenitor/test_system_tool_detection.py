"""Tests for system tool detection in decide node."""

import pytest
from app.progenitor.nodes.decide import (
    _looks_like_agent_discovery_request,
    _looks_like_self_reflect_request,
)


class TestAgentDiscoveryDetection:
    """Tests for _looks_like_agent_discovery_request heuristic."""

    @pytest.mark.parametrize("input_text", [
        "rewire agents",
        "rewire the agents",
        "rediscover agents",
        "rescan agents",
        "refresh agents",
        "reload agents",
        "scan agents",
        "discover agents",
        "refresh registry",
        "sync agents",
        "re-scan agents",
        "re-discover agents",
        "please rewire the agents",
        "can you rediscover agents?",
        "REWIRE AGENTS",  # Case insensitive
        "Refresh the agent registry",
        # New patterns for "agent discovery" as compound noun
        "run agent discovery",
        "can you run agent discovery",
        "agent discovery",
        "do agent discovery",
        "start agent discovery",
        "perform agent discovery",
        "please run agent discovery",
        "run discovery",
    ])
    def test_detects_discovery_requests(self, input_text):
        """Test that discovery requests are detected."""
        assert _looks_like_agent_discovery_request(input_text) is True

    @pytest.mark.parametrize("input_text", [
        "hello world",
        "what is the weather?",
        "build an agent",
        "create a new agent",
        "run the hello agent",
        "wire transfer",  # Should not match "wire"
        "scan files",  # "scan" without "agents"
        "",
        "   ",
        None,
    ])
    def test_does_not_detect_non_discovery_requests(self, input_text):
        """Test that non-discovery requests are not detected."""
        if input_text is None:
            # Test None input separately
            assert _looks_like_agent_discovery_request(input_text) is False
        else:
            assert _looks_like_agent_discovery_request(input_text) is False


class TestSelfReflectDetection:
    """Tests for _looks_like_self_reflect_request heuristic."""

    @pytest.mark.parametrize("input_text", [
        "how do you work",
        "how do you handle user input",
        "how do you process messages",
        "what is your architecture",
        "what are your capabilities",
        "explain your implementation",
        "describe your internals",
        "your source code",
        "how are you built",
        "how are you implemented",
        "self reflect",
        "self-reflect",
        "introspect yourself",
        "understand yourself",
        "examine yourself",
        "look at your code",
        "read your code",
        "your own code",
        "what can you do",
        "HOW DO YOU WORK",  # Case insensitive
        "How do you handle scheduling?",
    ])
    def test_detects_self_reflect_requests(self, input_text):
        """Test that self-reflection requests are detected."""
        assert _looks_like_self_reflect_request(input_text) is True

    @pytest.mark.parametrize("input_text", [
        "hello world",
        "what is the weather?",
        "build an agent",
        "create a new agent",
        "rewire agents",
        "list tools",
        "build status",
        "remind me at 9am",
        "schedule a task",
        "",
        "   ",
        None,
    ])
    def test_does_not_detect_non_self_reflect_requests(self, input_text):
        """Test that non-self-reflection requests are not detected."""
        if input_text is None:
            assert _looks_like_self_reflect_request(input_text) is False
        else:
            assert _looks_like_self_reflect_request(input_text) is False
