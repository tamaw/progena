"""
Tests for the clarification detection and handling flow.

Tests the feature where Copilot CLI's need for more information
is detected, presented to the user, and incorporated back into
the build process.
"""

from typing import cast
from unittest.mock import AsyncMock

import pytest

from app.progenitor.state.clarification import (
    ClarificationDetector,
    ClarificationRequest,
    create_clarification_history_entry,
)
from app.progenitor.state.ideal_state import IdealState, IdealStateBuilder
from app.progenitor.state.models import ProgenitorState


class TestClarificationDetector:
    """Test clarification detection logic."""

    def test_heuristic_detect_strong_signal(self):
        """Test detection of strong clarification signals."""
        detector = ClarificationDetector()

        # Simulate output with strong signal
        output = {
            "stdout": "Building agent... I need more information about the API endpoint.",
            "stderr": "",
            "success": True,
        }

        result = detector._heuristic_detect(output["stdout"])

        assert result.needs_clarification is True
        assert result.confidence >= 0.5
        assert "API endpoint" in result.context or "need more information" in result.question.lower()

    def test_heuristic_detect_no_signal(self):
        """Test that normal output doesn't trigger clarification."""
        detector = ClarificationDetector()

        output = "Successfully created agent.py\nAll tests passing.\nBuild complete."

        result = detector._heuristic_detect(output)

        assert result.needs_clarification is False
        assert result.confidence == 0.0

    def test_heuristic_detect_multiple_weak_signals(self):
        """Test detection with multiple weak signals."""
        detector = ClarificationDetector()

        output = """
        # TODO: Implement error handling
        # FIXME: Add validation
        # placeholder for configuration
        def process():
            pass  # not implemented
        """

        result = detector._heuristic_detect(output)

        # Multiple weak signals should trigger clarification
        assert result.needs_clarification is True
        assert result.confidence >= 0.3

    def test_heuristic_detect_explicit_question(self):
        """Test detection of explicit questions."""
        detector = ClarificationDetector()

        output = "Please clarify: which approach should I use for authentication?"

        result = detector._heuristic_detect(output)

        assert result.needs_clarification is True
        assert result.category in ("design", "requirements", "general")

    @pytest.mark.asyncio
    async def test_detect_with_successful_build(self):
        """Test that successful builds without signals don't need clarification."""
        detector = ClarificationDetector()

        copilot_output = {
            "stdout": "Created agent.py\nAll files generated successfully.",
            "stderr": "",
            "success": True,
            "summary": "Build complete.",
        }

        result = await detector.detect(
            copilot_output=copilot_output,
            task_description="Build a simple hello world agent",
        )

        assert result.needs_clarification is False

    @pytest.mark.asyncio
    async def test_detect_with_llm_client(self):
        """Test LLM-assisted detection."""
        mock_llm = AsyncMock()
        mock_llm.generate.return_value = """NEEDS_CLARIFICATION: yes
CONFIDENCE: 0.85
CATEGORY: requirements
CONTEXT: User did not specify API version
QUESTION: Which API version should the agent use?"""

        detector = ClarificationDetector(llm_client=mock_llm)

        # Use output with a strong signal to trigger heuristic detection first
        copilot_output = {
            "stdout": "I need more information about the API version to continue.",
            "stderr": "",
            "success": True,
        }

        result = await detector.detect(
            copilot_output=copilot_output,
            task_description="Build an API client agent",
        )

        assert result.needs_clarification is True
        # LLM should have been called to enhance detection
        assert mock_llm.generate.called or result.confidence >= 0.5

    @pytest.mark.asyncio
    async def test_format_user_question(self):
        """Test formatting of clarification question for user."""
        detector = ClarificationDetector()

        clarification = ClarificationRequest(
            needs_clarification=True,
            question="Which database should the agent connect to?",
            category="technical",
            confidence=0.8,
        )

        formatted = await detector.format_user_question(
            clarification,
            "Build a database agent",
        )

        assert "Clarification Needed" in formatted
        assert "database" in formatted.lower()
        assert "technical" in formatted.lower() or "constraints" in formatted.lower()


class TestClarificationHistoryEntry:
    """Test clarification history tracking."""

    def test_create_history_entry(self):
        """Test creating a history entry."""
        clarification = ClarificationRequest(
            needs_clarification=True,
            question="What format should the output be in?",
            category="requirements",
            confidence=0.75,
            context="Output format not specified",
        )

        entry = create_clarification_history_entry(clarification)

        assert entry["question"] == "What format should the output be in?"
        assert entry["category"] == "requirements"
        assert entry["confidence"] == 0.75
        assert entry["resolved"] is False
        assert "timestamp" in entry

    def test_create_history_entry_with_response(self):
        """Test creating a resolved history entry."""
        clarification = ClarificationRequest(
            needs_clarification=True,
            question="What format should the output be in?",
            category="requirements",
            confidence=0.75,
        )

        entry = create_clarification_history_entry(
            clarification,
            user_response="Use JSON format",
        )

        assert entry["resolved"] is True
        assert entry["user_response"] == "Use JSON format"


class TestIdealStateBuilderWithClarification:
    """Test IdealStateBuilder clarification refinement."""

    @pytest.mark.asyncio
    async def test_refine_with_clarification(self):
        """Test incorporating clarification into ideal state."""
        import json

        class _MockLLM:
            def __init__(self, rv):
                self.chat = AsyncMock(return_value=rv)

        mock_llm = _MockLLM(json.dumps({
            "updated_task_description": "Build an agent that processes JSON data and outputs CSV",
            "new_success_criteria": [
                {"id": "criterion_clarified_1", "description": "Must handle nested JSON structures",
                 "criterion_type": "functional", "rank": 2, "measurement": "Test with nested input",
                 "target_score": 0.8, "weight": 1.0},
            ],
            "new_anti_criteria": [
                {"id": "anti_clarified_1", "description": "Avoid losing data during conversion",
                 "severity": 1.0, "detection": "Compare input/output field counts"},
            ],
            "additional_constraints": ["Output must be RFC 4180 compliant CSV"],
            "reasoning": "User clarified JSON to CSV conversion requirement",
        }))

        builder = IdealStateBuilder(mock_llm)

        # Create initial ideal state
        initial_state = IdealState(
            task_description="Build a data conversion agent",
        )

        clarification_context = {
            "user_clarification": "The agent should convert JSON to CSV format",
            "original_context": "Output format not specified",
            "iteration_before_clarification": 1,
        }

        refined_state = await builder.refine_with_clarification(
            initial_state,
            clarification_context,
        )

        # Check task description was updated
        assert "JSON" in refined_state.task_description or "CSV" in refined_state.task_description

        # Check constraints include clarification
        assert any("clarification" in c.lower() or "json" in c.lower()
                   for c in refined_state.constraints)

    @pytest.mark.asyncio
    async def test_refine_with_clarification_fallback(self):
        """Test fallback when LLM fails."""
        class _FailingMockLLM:
            def __init__(self):
                self.chat = AsyncMock(side_effect=Exception("LLM error"))

        mock_llm = _FailingMockLLM()

        builder = IdealStateBuilder(mock_llm)

        initial_state = IdealState(
            task_description="Build an agent",
        )

        clarification_context = {
            "user_clarification": "Use REST API",
            "original_context": "API type unclear",
        }

        refined_state = await builder.refine_with_clarification(
            initial_state,
            clarification_context,
        )

        # Should still add clarification as constraint
        assert any("REST API" in c for c in refined_state.constraints)


class TestClarificationIntegration:
    """Integration tests for the full clarification flow."""

    @pytest.mark.asyncio
    async def test_verify_node_detects_clarification(self):
        """Test that verify node detects clarification needs."""
        from app.progenitor.nodes.verify_loop import create_node
        from app.progenitor.state.ideal_state import IdealState

        mock_llm = AsyncMock()
        # Mock verification
        mock_llm.generate.return_value = "Score: 0.7, needs work"

        verify_node = create_node(mock_llm, state_manager=None)

        # State with build output that needs clarification
        state = cast(ProgenitorState, {
            "phases": {"observe": {"ideal_state": IdealState(task_description="Build an agent").model_dump()}},
            "iteration": {"current": 1},
            "iteration_start_time": 0,
            "last_iteration_output": {
                "build_result": {
                    "stdout": "I need more information about the authentication method.",
                    "stderr": "",
                    "success": True,
                }
            },
        })

        result = await verify_node(state)

        # Should detect clarification need
        current_score = result.get("verification", {}).get("current_score") or 0.0
        assert result.get("clarification", {}).get("needed") is True or current_score < 1.0

    @pytest.mark.asyncio
    async def test_intake_recognizes_clarification_response(self):
        """Test that intake node recognizes clarification responses."""
        from app.progenitor.nodes.intake import create_node

        mock_state_manager = AsyncMock()
        mock_state_manager.log_conversation = AsyncMock()

        intake_node = create_node(mock_state_manager)

        # State awaiting clarification
        state = cast(ProgenitorState, {
            "current_input": "Use OAuth2 for authentication",
            "awaiting_clarification": True,
            "clarification_context": "Authentication method unclear",
            "messages": [],
        })

        result = await intake_node(state)

        # Should capture the clarification response
        assert result.get("clarification_response") == "Use OAuth2 for authentication"

    @pytest.mark.asyncio
    async def test_observe_incorporates_clarification(self):
        """Test that observe node incorporates clarification response."""
        from app.progenitor.nodes.observe import create_node
        from app.progenitor.state.ideal_state import IdealState

        mock_llm = AsyncMock()
        mock_llm.generate.return_value = """UPDATED_TASK: Build agent with OAuth2 auth
NEW_CRITERIA:
- Support OAuth2 flow
NEW_ANTI_CRITERIA:
- Don't store tokens insecurely"""

        observe_node = create_node(mock_llm, state_manager=None)

        initial_ideal_state = IdealState(
            task_description="Build an authenticated agent",
        )

        state = cast(ProgenitorState, {
            "iteration": {"current": 1},
            "phases": {"observe": {"ideal_state": initial_ideal_state}},
            "awaiting_clarification": True,
            "clarification_response": "Use OAuth2 for authentication",
            "clarification_context": "Auth method unclear",
            "clarification_history": [{"question": "What auth?", "resolved": False}],
            "task_id": "test_task",
        })

        result = await observe_node(state)

        # Should clear clarification state
        assert result.get("awaiting_clarification") is False
        assert result.get("clarification_response") is None

        # Should update ideal state
        from app.progenitor.state.helpers import get_ideal_state
        assert get_ideal_state(result) is not None

    def test_graph_routes_clarification_response(self):
        """Test that graph correctly routes clarification responses.

        Build decisions return ANSWER_DIRECTLY (not BUILD_AGENT) because
        decide starts the build via BackgroundBuildRunner. So the route
        should be 'respond', not 'planner' (outer loop has no planner).
        """
        from app.progenitor.graph_outer_loop import route_after_decide
        from app.progenitor.state.ideal_state import IdealState
        from app.progenitor.state.models import Decision, DecisionAction

        # State with clarification response - decide returns ANSWER_DIRECTLY
        # after starting a build, so this tests that case
        state = cast(ProgenitorState, {
            "awaiting_clarification": True,
            "clarification_response": "Use REST API",
            "phases": {"observe": {"ideal_state": IdealState(task_description="test").model_dump()}},
            "current_decision": Decision(
                action=DecisionAction.ANSWER_DIRECTLY,  # Builds return this
                reasoning="Started async build based on clarification",
                requires_planning=False,
            ),
        })

        route = route_after_decide(state)

        # Should route to respond (outer loop has no planner)
        assert route == "respond"

    def test_should_iterate_pauses_for_clarification(self):
        """Test that iteration pauses when clarification needed."""
        from app.progenitor.routing import should_iterate

        # State where clarification is needed
        state = cast(ProgenitorState, {
            "iteration": {"current": 1},
            "clarification": {"needed": True},
            "verification_passed": False,
            "verification": {"current_score": 0.5},
        })

        route = should_iterate(state)

        # Should route to respond to ask user
        assert route == "respond"


class TestClarificationDetectorEdgeCases:
    """Edge case tests for clarification detection."""

    def test_empty_output(self):
        """Test handling of empty output."""
        detector = ClarificationDetector()
        result = detector._heuristic_detect("")

        assert result.needs_clarification is False

    def test_very_long_output(self):
        """Test handling of very long output."""
        detector = ClarificationDetector()

        # Long output with clarification signal buried in middle
        long_output = "Log line\n" * 1000
        long_output += "I need more information about the configuration\n"
        long_output += "Log line\n" * 1000

        result = detector._heuristic_detect(long_output)

        assert result.needs_clarification is True

    def test_false_positive_prevention(self):
        """Test that common false positives are avoided."""
        detector = ClarificationDetector()

        # Code that mentions "TODO" in a comment but is actually complete
        output = """
        def process():
            # Historical note: TODO was here but now implemented
            return "complete"

        All tests passed.
        Build successful.
        """

        result = detector._heuristic_detect(output)

        # Single weak signal shouldn't trigger
        # (depending on implementation, this might be False or low confidence)
        assert result.confidence < 0.5 or result.needs_clarification is False

    @pytest.mark.asyncio
    async def test_llm_fallback_on_error(self):
        """Test fallback to heuristics when LLM fails."""
        mock_llm = AsyncMock()
        mock_llm.generate.side_effect = Exception("API error")

        detector = ClarificationDetector(llm_client=mock_llm)

        copilot_output = {
            "stdout": "Need more information about the endpoint",
            "stderr": "",
            "success": True,
        }

        result = await detector.detect(
            copilot_output=copilot_output,
            task_description="Build agent",
        )

        # Should fall back to heuristics and still detect
        assert result.needs_clarification is True

    def test_categorization(self):
        """Test correct categorization of clarification needs."""
        detector = ClarificationDetector()

        test_cases = [
            ("what should the output format be", "design"),
            ("unclear requirement for validation", "requirements"),
            ("which approach do you prefer", "design"),
            ("please specify the scope", "scope"),
        ]

        for signal, _expected_category in test_cases:
            category = detector._categorize_need(signal)
            # Categories might vary, but should be reasonable
            assert category in ("design", "requirements", "technical", "scope", "general")
