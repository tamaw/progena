"""Tests for active state models."""

from datetime import UTC, datetime

from app.progenitor.state.models import (
    AgentRegistryEntry,
    ConversationMessage,
    ScheduleEntry,
    ScheduleRunRecord,
    generate_schedule_thread_id,
    redact_secrets,
)


class TestScheduleEntry:
    def test_create_recurring_schedule(self):
        entry = ScheduleEntry(
            id="schedule-1",
            message="Say good morning",
            cron_expression="0 9 * * *",
        )

        assert entry.cron_expression == "0 9 * * *"
        assert entry.message == "Say good morning"

    def test_schedule_with_last_run(self):
        run_time = datetime(2026, 1, 20, 10, 0, 0, tzinfo=UTC)
        entry = ScheduleEntry(
            id="schedule-2",
            message="Send the report",
            cron_expression="0 10 20 1 *",
            last_run=run_time,
        )

        assert entry.last_run == run_time


class TestScheduleRunRecord:
    def test_create_schedule_run_record(self):
        record = ScheduleRunRecord(
            run_id="schedrun_123",
            schedule_id="cron_schedule123",
            thread_id="cron_schedule123_abcd1234",
            message="Say hello",
        )

        assert record.status == "queued"
        assert record.schedule_id == "cron_schedule123"

    def test_generate_schedule_thread_id_derives_from_schedule(self):
        thread_id = generate_schedule_thread_id("sched_daily-report")

        assert thread_id.startswith("sched_daily_report_")


class TestAgentRegistryEntry:
    def test_create_agent_entry(self):
        entry = AgentRegistryEntry(
            name="email_agent",
            description="Handles email operations",
            capabilities=["send_email", "read_email"],
            entrypoint="/app/agents/email/main.py",
        )

        assert entry.name == "email_agent"
        assert "send_email" in entry.capabilities
        assert entry.kind == "a2a_agent"
        assert entry.transport == "http"
        assert entry.status.value == "stopped"

    def test_create_minimal_agent_entry(self):
        entry = AgentRegistryEntry(name="minimal_agent")

        assert entry.name == "minimal_agent"
        assert entry.description == ""
        assert entry.entrypoint == ""


class TestConversationMessage:
    def test_create_user_message(self):
        msg = ConversationMessage(
            id="msg-1",
            role="user",
            content="Hello, Progenitor!",
        )

        assert msg.role == "user"
        assert msg.content == "Hello, Progenitor!"
        assert msg.timestamp is not None

    def test_create_assistant_message(self):
        msg = ConversationMessage(
            id="msg-2",
            role="assistant",
            content="Hello! How can I help you?",
            metadata={"source": "deep_agent"},
        )

        assert msg.role == "assistant"
        assert msg.metadata["source"] == "deep_agent"


class TestSecretRedaction:
    def test_redact_api_key(self):
        text = 'config: {"api_key": "super-secret-key123"}'
        result = redact_secrets(text)

        assert "super-secret-key123" not in result
        assert "REDACTED" in result

    def test_redact_password(self):
        text = 'password="mypassword123"'
        result = redact_secrets(text)

        assert "mypassword123" not in result
        assert "REDACTED" in result

    def test_redact_bearer_token(self):
        text = "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
        result = redact_secrets(text)

        assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in result
        assert "REDACTED" in result

    def test_redact_openai_key(self):
        text = "using key: sk-abc123xyz789defghijklmnopqrstuvwxyz"
        result = redact_secrets(text)

        assert "sk-abc123xyz789defghijklmnopqrstuvwxyz" not in result
        assert "OPENAI_KEY_REDACTED" in result

    def test_preserve_non_secret_content(self):
        text = "Hello, this is a normal message with no secrets."
        result = redact_secrets(text)

        assert result == text
