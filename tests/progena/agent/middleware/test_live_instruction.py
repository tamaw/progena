"""An edited system instruction reaches the model without rebuilding the graph."""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest
from langchain.agents.middleware.types import ModelRequest
from langchain_core.messages import SystemMessage

from src.progena.agent.clock_context import user_clock_context
from src.progena.agent.middleware import LiveInstructionMiddleware


class Instructions:
    def __init__(self, content: str) -> None:
        self.content = content
        self.preferences: dict = {}

    async def get_content_document(self, kind: str, path: str) -> str | None:
        assert (kind, path) == ("instruction", "progena/genesis.md")
        return self.content

    async def get_preferences_config(self) -> dict:
        return self.preferences


def _instruction(body: str) -> str:
    return f"---\ntitle: Genesis\ndescription: Test\n---\n{body}\n"


async def _system_text_sent(middleware: LiveInstructionMiddleware, system: SystemMessage) -> str:
    seen: list[ModelRequest] = []

    async def handler(request: ModelRequest):
        seen.append(request)
        return MagicMock()

    await middleware.awrap_model_call(
        ModelRequest(model=MagicMock(), messages=[], tools=[], system_message=system), handler
    )
    return "".join(
        block["text"] for block in seen[0].system_message.content_blocks if block["type"] == "text"
    )


async def test_edited_instruction_replaces_the_boot_text_and_keeps_appended_sections():
    store = Instructions(_instruction("You are Progena."))
    middleware = LiveInstructionMiddleware(store, "progena/genesis.md", "You are Progena.")
    system = SystemMessage(
        content_blocks=[
            {"type": "text", "text": "You are Progena.\n\nBase agent prompt."},
            {"type": "text", "text": "\n\nSkills section."},
        ]
    )
    initial = await _system_text_sent(middleware, system)
    assert initial.startswith("You are Progena.\n\nBase agent prompt.\n\nSkills section.")

    store.content = _instruction("You are Progena, edited.")
    edited = await _system_text_sent(middleware, system)
    assert edited.startswith("You are Progena, edited.\n\nBase agent prompt.\n\nSkills section.")


async def test_renamed_agent_reaches_the_next_model_call():
    store = Instructions(_instruction("You are {agent_name}."))
    middleware = LiveInstructionMiddleware(store, "progena/genesis.md", "You are Progena.")
    system = SystemMessage(content_blocks=[{"type": "text", "text": "You are Progena.\n\nBase."}])

    store.preferences = {"agent_name": "Ada", "timezone": "Australia/Melbourne"}
    rendered = await _system_text_sent(middleware, system)
    assert rendered.startswith("You are Ada.\n\nBase.")
    assert "User timezone: Australia/Melbourne; today there is " in rendered


@pytest.mark.parametrize(
    ("has_shell", "has_current_time", "present", "absent"),
    [
        (True, False, "`date -u`", "`current_time`"),
        (False, True, "`current_time`", "`date -u`"),
        (False, False, "No exact-time tool", "`current_time`"),
    ],
)
async def test_clock_guidance_matches_available_capabilities(
    has_shell, has_current_time, present, absent
):
    context = await user_clock_context(
        Instructions(_instruction("You are Progena.")),
        has_shell=has_shell,
        has_current_time=has_current_time,
    )
    assert present in context
    assert absent not in context


async def test_user_date_changes_at_preference_midnight_not_server_midnight():
    store = Instructions(_instruction("You are Progena."))
    store.preferences = {"timezone": "Australia/Melbourne"}
    with patch("src.progena.agent.clock_context.datetime") as clock:
        clock.now.return_value = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)
        early = await user_clock_context(store, has_shell=False)
        clock.now.return_value = datetime(2026, 10, 28, 12, 59, tzinfo=UTC)
        later = await user_clock_context(store, has_shell=False)
        clock.now.return_value = datetime(2026, 10, 28, 13, 0, tzinfo=UTC)
        tomorrow = await user_clock_context(store, has_shell=False)
    assert early == later
    assert "2026-10-28" in early
    assert "2026-10-29" in tomorrow
