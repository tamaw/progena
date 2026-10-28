"""An edited system instruction reaches the model without rebuilding the graph."""

from unittest.mock import MagicMock

from langchain.agents.middleware.types import ModelRequest
from langchain_core.messages import SystemMessage

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
    assert initial.startswith("You are Progena.\n\nUser timezone: UTC; today there is ")
    assert initial.endswith("\n\nBase agent prompt.\n\nSkills section.")

    store.content = _instruction("You are Progena, edited.")
    edited = await _system_text_sent(middleware, system)
    assert edited.startswith("You are Progena, edited.\n\nUser timezone: UTC; today there is ")
    assert edited.endswith("\n\nBase agent prompt.\n\nSkills section.")


async def test_renamed_agent_reaches_the_next_model_call():
    store = Instructions(_instruction("You are {agent_name}."))
    middleware = LiveInstructionMiddleware(store, "progena/genesis.md", "You are Progena.")
    system = SystemMessage(content_blocks=[{"type": "text", "text": "You are Progena.\n\nBase."}])

    store.preferences = {"agent_name": "Ada", "timezone": "Australia/Melbourne"}
    rendered = await _system_text_sent(middleware, system)
    assert rendered.startswith(
        "You are Ada.\n\nUser timezone: Australia/Melbourne; today there is "
    )
    assert rendered.endswith("\n\nBase.")
