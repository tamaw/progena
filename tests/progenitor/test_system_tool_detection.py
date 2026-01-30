"""Tests for system tool detection in decide node."""

import pytest
from app.progenitor.nodes.decide import _looks_like_agent_discovery_request


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
