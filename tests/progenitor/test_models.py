"""Tests for state models."""

from datetime import UTC, datetime

from app.progenitor.state.models import (
    AgentRegistryEntry,
    ConversationMessage,
    Decision,
    DecisionAction,
    ScheduleEntry,
    ValidationResult,
    WorkItem,
    WorkItemStatus,
    WorkItemType,
)


class TestWorkItem:
    """Tests for WorkItem model."""

    def test_create_work_item(self):
        """Test creating a basic work item."""
        item = WorkItem(
            id="test-123",
            item_type=WorkItemType.BUILD_AGENT,
            description="Build a test agent",
        )

        assert item.id == "test-123"
        assert item.item_type == WorkItemType.BUILD_AGENT
        assert item.status == WorkItemStatus.PENDING
        assert item.description == "Build a test agent"
        assert item.retry_count == 0

    def test_work_item_with_context(self):
        """Test work item with additional context."""
        item = WorkItem(
            id="test-456",
            item_type=WorkItemType.A2A_CALL,
            description="Call email agent",
            target_agent="email_agent",
            context={"recipient": "john@example.com"},
        )

        assert item.target_agent == "email_agent"
        assert item.context["recipient"] == "john@example.com"


class TestDecision:
    """Tests for Decision model."""

    def test_create_decision(self):
        """Test creating a routing decision."""
        decision = Decision(
            action=DecisionAction.BUILD_AGENT,
            reasoning="User explicitly asked to build an agent",
            target_capabilities=["weather_agent"],
        )

        assert decision.action == DecisionAction.BUILD_AGENT
        assert "weather_agent" in decision.target_capabilities
        assert not decision.requires_planning

    def test_direct_answer_decision(self):
        """Test direct answer decision."""
        decision = Decision(
            action=DecisionAction.ANSWER_DIRECTLY,
            reasoning="Simple question",
            direct_response="The answer is 42",
        )

        assert decision.action == DecisionAction.ANSWER_DIRECTLY
        assert decision.direct_response == "The answer is 42"


class TestScheduleEntry:
    """Tests for ScheduleEntry model (simplified)."""

    def test_create_recurring_schedule(self):
        """Test creating a recurring schedule with repeat_minutes."""
        run_at = datetime(2026, 1, 20, 9, 0, 0, tzinfo=UTC)
        entry = ScheduleEntry(
            id="schedule-1",
            description="Daily reminder",
            instruction="Say good morning",
            run_at=run_at,
            repeat_minutes=1440,  # Daily
        )

        assert entry.repeat_minutes == 1440
        assert entry.enabled

    def test_create_one_off_schedule(self):
        """Test creating a one-off schedule."""
        run_time = datetime(2026, 1, 20, 10, 0, 0, tzinfo=UTC)
        entry = ScheduleEntry(
            id="schedule-2",
            description="One-time reminder",
            instruction="Send the report",
            run_at=run_time,
        )

        assert entry.run_at == run_time
        assert entry.repeat_minutes is None


class TestAgentRegistryEntry:
    """Tests for AgentRegistryEntry model."""

    def test_create_agent_entry(self):
        """Test creating an agent registry entry."""
        entry = AgentRegistryEntry(
            name="email_agent",
            description="Handles email operations",
            capabilities=["send_email", "read_email"],
            entrypoint="/app/agents/email/main.py",
        )

        assert entry.name == "email_agent"
        assert "send_email" in entry.capabilities
        assert entry.kind == "a2a_agent"
        assert entry.transport == "http"  # Default is HTTP (A2A standard)
        assert entry.status.value == "stopped"  # Default status


class TestConversationMessage:
    """Tests for ConversationMessage model."""

    def test_create_user_message(self):
        """Test creating a user message."""
        msg = ConversationMessage(
            id="msg-1",
            role="user",
            content="Hello, Progenitor!",
        )

        assert msg.role == "user"
        assert msg.content == "Hello, Progenitor!"
        assert msg.timestamp is not None

    def test_create_assistant_message(self):
        """Test creating an assistant message."""
        msg = ConversationMessage(
            id="msg-2",
            role="assistant",
            content="Hello! How can I help you?",
            metadata={"decision_action": "answer_directly"},
        )

        assert msg.role == "assistant"
        assert msg.metadata["decision_action"] == "answer_directly"


class TestValidationResult:
    """Tests for ValidationResult model."""

    def test_successful_validation(self):
        """Test successful validation result."""
        result = ValidationResult(
            success=True,
            work_item_id="item-123",
            checks_run=["syntax", "pytest"],
        )

        assert result.success
        assert "syntax" in result.checks_run
        assert len(result.errors) == 0

    def test_failed_validation(self):
        """Test failed validation result."""
        result = ValidationResult(
            success=False,
            work_item_id="item-456",
            checks_run=["syntax"],
            errors=["SyntaxError in main.py: line 42"],
        )

        assert not result.success
        assert len(result.errors) == 1


class TestSecretRedaction:
    """Tests for secret redaction functionality."""

    def test_redact_api_key(self):
        """Test redacting API keys."""
        from app.progenitor.state.models import redact_secrets

        text = 'config: {"api_key": "super-secret-key123"}'
        result = redact_secrets(text)

        assert "super-secret-key123" not in result
        assert "REDACTED" in result

    def test_redact_password(self):
        """Test redacting passwords."""
        from app.progenitor.state.models import redact_secrets

        text = 'password="mypassword123"'
        result = redact_secrets(text)

        assert "mypassword123" not in result
        assert "REDACTED" in result

    def test_redact_bearer_token(self):
        """Test redacting bearer tokens."""
        from app.progenitor.state.models import redact_secrets

        text = 'Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9'
        result = redact_secrets(text)

        assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in result
        assert "REDACTED" in result

    def test_redact_openai_key(self):
        """Test redacting OpenAI API keys."""
        from app.progenitor.state.models import redact_secrets

        text = 'using key: sk-abc123xyz789defghijklmnopqrstuvwxyz'
        result = redact_secrets(text)

        assert "sk-abc123xyz789defghijklmnopqrstuvwxyz" not in result
        assert "OPENAI_KEY_REDACTED" in result

    def test_preserve_non_secret_content(self):
        """Test that non-secret content is preserved."""
        from app.progenitor.state.models import redact_secrets

        text = 'Hello, this is a normal message with no secrets.'
        result = redact_secrets(text)

        assert result == text
