"""Tests for LangGraph nodes."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.progenitor.state.models import (
    Decision,
    DecisionAction,
    ProgenitorState,
    WorkItem,
    WorkItemStatus,
    WorkItemType,
)


class TestIntakeNode:
    """Tests for the intake node."""

    @pytest.mark.asyncio
    async def test_intake_logs_user_message(self):
        """Test that intake logs user messages."""
        from app.progenitor.nodes import intake

        mock_state_manager = AsyncMock()
        node = intake.create_node(mock_state_manager)

        state: ProgenitorState = {
            "current_input": "Hello, Progenitor!",
            "messages": [],
        }

        result = await node(state)

        # Should have called log_conversation
        mock_state_manager.log_conversation.assert_called_once()

        # Should have added message to state
        messages = result.get("messages")
        assert messages is not None
        assert len(messages) == 1
        assert messages[0].content == "Hello, Progenitor!"
        assert messages[0].role == "user"

    @pytest.mark.asyncio
    async def test_intake_no_input_passthrough(self):
        """Test that intake passes through when no input."""
        from app.progenitor.nodes import intake

        mock_state_manager = AsyncMock()
        node = intake.create_node(mock_state_manager)

        state: ProgenitorState = {
            "current_input": None,
            "messages": [],
        }

        result = await node(state)

        # Should not log anything
        mock_state_manager.log_conversation.assert_not_called()
        assert result == {}


class TestDecideNode:
    """Tests for the decide node."""

    @pytest.mark.asyncio
    async def test_decide_direct_answer(self):
        """Test decision for direct answer."""
        from app.progenitor.runtime import decide

        mock_llm = AsyncMock()
        mock_llm.decide.return_value = {
            "action": "answer_directly",
            "reasoning": "Simple question",
            "direct_response": "The answer is 42",
        }

        mock_registry = MagicMock()
        mock_registry.get_all_capabilities.return_value = []

        mock_state_manager = AsyncMock()

        node = decide.create_node(mock_llm, mock_registry, mock_state_manager)

        state: ProgenitorState = {
            "current_input": "What is 6 times 7?",
            "messages": [],
        }

        result = await node(state)

        decision = result.get("current_decision")
        assert decision is not None
        assert decision.action == DecisionAction.ANSWER_DIRECTLY

    @pytest.mark.asyncio
    async def test_decide_forces_build_agent_for_explicit_agent_request(self):
        """Explicit build-agent requests should trigger background build flow."""
        from app.progenitor.runtime import decide

        # Use MagicMock as base so sync methods like _get_json_response_format don't return coroutines
        mock_llm = MagicMock()
        # Simulate an LLM response that identifies this as a build request with high confidence.
        mock_llm.decide = AsyncMock(return_value={
            "action": "build_agent",
            "confidence": 0.98,  # Above 0.95 threshold
            "reasoning": "User explicitly asked to build an agent",
            "requires_planning": True,
            "target_capabilities": ["hello_world_a2a_agent"],
            "user_response": "I'll start building a hello world agent for you.",
        })
        # Mock sync method and async method needed by _generate_build_settings
        mock_llm._get_json_response_format = MagicMock(return_value={"type": "json_object"})
        mock_llm.complete = AsyncMock(return_value='{"project_name": "hello_world", "build_type": "agent", "reasoning": "test"}')

        mock_registry = MagicMock()
        mock_registry.get_all_capabilities.return_value = []

        mock_state_manager = AsyncMock()

        node = decide.create_node(mock_llm, mock_registry, mock_state_manager)

        state: ProgenitorState = {
            "current_input": "Please build a hello world agent and register it.",
            "messages": [],
        }

        result = await node(state)

        decision = result.get("current_decision")
        assert decision is not None
        assert decision.action == DecisionAction.ANSWER_DIRECTLY
        assert decision.requires_planning is False
        # Without a background runner, we should get the "unavailable" message
        assert "Background Build Unavailable" in (decision.direct_response or "")

    @pytest.mark.asyncio
    async def test_decide_rejects_low_confidence_build(self):
        """Test that build actions with low confidence are rejected."""
        from app.progenitor.runtime import decide

        mock_llm = AsyncMock()
        # Simulate an LLM response with build action but LOW confidence
        mock_llm.decide.return_value = {
            "action": "build_agent",
            "confidence": 0.6,  # Below 0.95 threshold
            "reasoning": "Maybe user wants to build?",
            "requires_planning": True,
            "target_capabilities": [],
            "user_response": None,
        }

        mock_registry = MagicMock()
        mock_registry.get_all_capabilities.return_value = []

        mock_state_manager = AsyncMock()

        node = decide.create_node(mock_llm, mock_registry, mock_state_manager)

        state: ProgenitorState = {
            "current_input": "I wonder if you could construct something for me",
            "messages": [],
        }

        result = await node(state)

        decision = result.get("current_decision")
        assert decision is not None
        assert decision.action == DecisionAction.ANSWER_DIRECTLY
        # Should explain that confidence was too low
        assert "not certain" in (decision.direct_response or "").lower() or "confidence" in (decision.direct_response or "").lower()

    @pytest.mark.asyncio
    async def test_decide_no_input(self):
        """Test decision with no input."""
        from app.progenitor.runtime import decide

        mock_llm = AsyncMock()
        mock_registry = MagicMock()

        node = decide.create_node(mock_llm, mock_registry)

        state: ProgenitorState = {
            "current_input": None,
            "backlog": [],
        }

        result = await node(state)

        assert result.get("current_decision") is None

    @pytest.mark.asyncio
    async def test_decide_answer_direct_pass_through(self):
        """ANSWER_DIRECTLY should be a pass-through: direct_response=None so the chat loop responds."""
        from app.progenitor.runtime import decide

        mock_llm = MagicMock()
        mock_llm.decide = AsyncMock(return_value={
            "action": "answer_directly",
            "reasoning": "Simple question",
            "requires_planning": False,
            "target_capabilities": [],
        })

        mock_registry = MagicMock()
        mock_registry.get_tool_context.return_value = ""
        mock_state_manager = AsyncMock()

        node = decide.create_node(mock_llm, mock_registry, mock_state_manager)

        state: ProgenitorState = {
            "current_input": "Say hi",
            "messages": [],
        }

        result = await node(state)

        decision = result.get("current_decision")
        assert decision is not None
        assert decision.action == DecisionAction.ANSWER_DIRECTLY
        # Pass-through: decide node does NOT generate a response — the chat loop does.
        assert decision.direct_response is None

    @pytest.mark.asyncio
    async def test_decide_slow_path_structured_omits_tools_param(self):
        """Structured decide routing should not force tools=[] at the call site.

        The LLM client is responsible for suppressing tool calling for structured
        outputs (e.g., via tool_choice='none') while still sending the tool list
        upstream.
        """
        from app.progenitor.llm.response_models import DecisionResponse
        from app.progenitor.runtime import decide

        mock_llm = MagicMock()
        mock_llm.chat_structured = AsyncMock(return_value=DecisionResponse(
            action="answer_directly",
            confidence=0.9,
            reasoning="Greeting",
            requires_planning=False,
            target_capabilities=[],
        ))
        mock_llm.chat = AsyncMock(return_value="Hi there!")

        mock_registry = MagicMock()
        mock_registry.get_tool_context.return_value = ""
        mock_state_manager = AsyncMock()

        node = decide.create_node(mock_llm, mock_registry, mock_state_manager)

        state: ProgenitorState = {
            "current_input": "hi",
            "messages": [],
        }

        result = await node(state)
        decision = result.get("current_decision")
        assert decision is not None
        assert decision.action == DecisionAction.ANSWER_DIRECTLY
        assert mock_llm.chat_structured.await_args.kwargs.get("tools") is None

    def test_extract_history_includes_system_messages(self):
        """History extraction should include system messages passed in the message list."""
        from app.progenitor.runtime import decide

        history = decide._extract_history(
            [
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "hello"},
            ],
            limit=10,
        )

        assert len(history) == 2
        assert history[0]["role"] == "system"
        assert history[1]["role"] == "user"


class TestPickNextNode:
    """Tests for the pick_next node."""

    @pytest.mark.asyncio
    async def test_pick_next_selects_pending(self):
        """Test that pick_next selects first pending item."""
        from app.progenitor.nodes import pick_next

        node = pick_next.create_node()

        state: ProgenitorState = {
            "backlog": [
                WorkItem(
                    id="item-1",
                    item_type=WorkItemType.A2A_CALL,
                    status=WorkItemStatus.COMPLETED,
                    description="Done",
                ),
                WorkItem(
                    id="item-2",
                    item_type=WorkItemType.BUILD_AGENT,
                    status=WorkItemStatus.PENDING,
                    description="Pending",
                ),
            ],
        }

        result = await node(state)

        current_item = result.get("current_work_item")
        assert current_item is not None
        assert current_item.id == "item-2"

    @pytest.mark.asyncio
    async def test_pick_next_no_pending(self):
        """Test pick_next when no pending items."""
        from app.progenitor.nodes import pick_next

        node = pick_next.create_node()

        state: ProgenitorState = {
            "backlog": [
                WorkItem(
                    id="item-1",
                    item_type=WorkItemType.A2A_CALL,
                    status=WorkItemStatus.COMPLETED,
                    description="Done",
                ),
            ],
        }

        result = await node(state)

        assert result.get("current_work_item") is None


class TestRespondNode:
    """Tests for the respond node."""

    @pytest.mark.asyncio
    async def test_respond_direct_answer(self):
        """Test responding with direct answer."""
        from app.progenitor.nodes import respond

        mock_state_manager = AsyncMock()
        node = respond.create_node(mock_state_manager)

        state: ProgenitorState = {
            "current_decision": Decision(
                action=DecisionAction.ANSWER_DIRECTLY,
                reasoning="Simple",
                direct_response="Hello there!",
            ),
            "messages": [],
        }

        result = await node(state)

        assert result.get("pending_response") == "Hello there!"
        mock_state_manager.log_conversation.assert_called_once()

    @pytest.mark.asyncio
    async def test_respond_work_completed(self):
        """Test responding for completed work."""
        from app.progenitor.nodes import respond

        mock_state_manager = AsyncMock()
        node = respond.create_node(mock_state_manager)

        completed_item = WorkItem(
            id="item-1",
            item_type=WorkItemType.A2A_CALL,
            status=WorkItemStatus.COMPLETED,
            description="Send email",
            result={"message": "Email sent successfully"},
        )

        state: ProgenitorState = {
            "current_decision": None,
            "current_work_item": completed_item,
            "messages": [],
        }

        result = await node(state)

        pending = result.get("pending_response")
        assert pending is not None
        assert "Email sent successfully" in pending


class TestCommitCoreNode:
    """Tests for the commit_core node."""

    @pytest.mark.asyncio
    async def test_commit_saves_backlog(self):
        """Test that commit saves backlog and schedule.

        Note: schedule is saved to schedule.json only (system feature),
        NOT to current_state.json (which is user session state).
        """
        from app.progenitor.nodes import commit_core

        mock_state_manager = AsyncMock()
        node = commit_core.create_node(mock_state_manager)

        state: ProgenitorState = {
            "backlog": [
                WorkItem(
                    id="item-1",
                    item_type=WorkItemType.A2A_CALL,
                    description="Test",
                ),
            ],
            "schedule": [],
            "cycle_count": 5,
            "actions_this_cycle": 2,
            "run_id": "test-run",
        }

        result = await node(state)

        mock_state_manager.save_backlog.assert_called_once()
        mock_state_manager.save_schedule.assert_called_once()  # Schedule saved to schedule.json only
        mock_state_manager.update_current_state.assert_called_once()

        assert "last_commit_time" in result


class TestCycleLimitNode:
    """Tests for the cycle_limit node."""

    @pytest.mark.asyncio
    async def test_cycle_limit_under_limit(self):
        """Test cycle limit when under limit."""
        from app.progenitor.config import ProgenitorConfig, RuntimeConfig
        from app.progenitor.nodes import cycle_limit

        # Create a minimal config
        config = ProgenitorConfig(
            runtime=RuntimeConfig(max_actions_per_cycle=10)
        )
        node = cycle_limit.create_node(config)

        state: ProgenitorState = {
            "actions_this_cycle": 3,
        }

        result = await node(state)

        # Should not force pause
        assert "current_work_item" not in result

    @pytest.mark.asyncio
    async def test_cycle_limit_at_limit(self):
        """Test cycle limit when at limit."""
        from app.progenitor.config import ProgenitorConfig, RuntimeConfig
        from app.progenitor.nodes import cycle_limit

        # Create config with low limit
        config = ProgenitorConfig(
            runtime=RuntimeConfig(max_actions_per_cycle=5)
        )
        node = cycle_limit.create_node(config)

        state: ProgenitorState = {
            "actions_this_cycle": 5,
        }

        result = await node(state)

        # Should force pause
        assert result.get("current_work_item") is None
        assert result.get("actions_this_cycle") == 0
