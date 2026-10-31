"""Runtime memory requires a durable content-document owner."""

from pathlib import Path

import pytest
from deepagents.backends import StateBackend
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from pydantic import Field

from src.progena.agent import factory
from src.progena.agent.factory import create_progena_agent
from src.progena.config import LLMConfig, ProgenaConfig
from tests.progena.conftest import _FakeRuntimeStore


class FakeContentStateManager:
    """Minimal async state_manager stub for get/save/list content documents."""

    def __init__(self, *, prompt: str = "GENESIS") -> None:
        self._runtime_store = _FakeRuntimeStore()
        self._docs: dict[str, dict[str, str]] = {
            "instruction": {
                "progena/genesis.md": f"---\ntitle: Genesis\ndescription: Test\n---\n{prompt}"
            },
            "skill": {},
            "agent": {},
            "memory": {},
        }

    async def get_content_document(self, kind: str, relative_path: str) -> str | None:
        return self._docs[kind].get(relative_path)

    async def save_content_document(self, kind: str, relative_path: str, content: str) -> None:
        self._docs[kind][relative_path] = content

    async def delete_content_document(self, kind: str, relative_path: str) -> None:
        self._docs[kind].pop(relative_path, None)

    async def list_content_documents(self, kind: str) -> list[tuple[str, str]]:
        return list(self._docs[kind].items())

    async def list_routine_graphs(self) -> list[dict]:
        return []

    async def get_preferences_config(self) -> dict:
        return {}


async def test_factory_keeps_async_subagent_tools_on_the_assembled_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The live middleware, not descriptors alone, provides async task tools."""

    class Model(FakeMessagesListChatModel):
        bound_tool_names: list[str] = Field(default_factory=list)

        def bind_tools(self, tools, **kwargs):  # type: ignore[override]
            del kwargs
            self.bound_tool_names = [tool.name for tool in tools]
            return self

    model = Model(responses=[AIMessage(content="ready")])
    monkeypatch.setattr(factory, "ProviderChatModel", lambda **_kwargs: model)
    monkeypatch.setattr(factory, "create_progena_backend", lambda **_kwargs: StateBackend())
    project_root = Path(__file__).parents[2]
    agent = await create_progena_agent(
        config=ProgenaConfig(llm=LLMConfig(provider_id="test-provider", reasoning_enabled=False)),
        checkpointer=MemorySaver(),
        state_manager=FakeContentStateManager(),
        project_root=str(project_root),
    )

    await agent.ainvoke(
        {"messages": [HumanMessage(content="hello")]},
        {"configurable": {"thread_id": "factory-async-tools"}},
    )

    assert {
        "start_async_task",
        "check_async_task",
        "update_async_task",
        "cancel_async_task",
        "list_async_tasks",
    } <= set(model.bound_tool_names)


async def test_factory_requires_state_manager():
    with pytest.raises(ValueError):
        await create_progena_agent()
