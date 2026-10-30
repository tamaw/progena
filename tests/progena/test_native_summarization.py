from __future__ import annotations

import uuid

import pytest
from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, StateBackend
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver

from src.progena.agent.middleware.summarization import (
    ProgenaSummarizationMiddleware,
    install_progena_summarizer_profile,
)
from src.progena.sessions.summarization import SUMMARY_MARKER_KEY, summary_body


class ProviderChatModel(FakeMessagesListChatModel):
    """Use the same DeepAgents profile key as Progena's real model adapter."""

    @property
    def _llm_type(self) -> str:
        return "progena_provider"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, *args, **kwargs):
        result = super()._generate(*args, **kwargs)
        for generation in result.generations:
            generation.message = generation.message.model_copy(update={"id": uuid.uuid4().hex})
        return result


@pytest.mark.asyncio
async def test_native_deepagent_compaction_uses_one_progena_summary_middleware() -> None:
    install_progena_summarizer_profile()
    backend = CompositeBackend(default=StateBackend(), routes={"/conversations/": StateBackend()})
    model = ProviderChatModel(
        profile={"max_input_tokens": 20000},
        responses=[AIMessage(content="Compressed context."), AIMessage(content="Continued.")],
    )
    summarization = ProgenaSummarizationMiddleware(model=model, backend=backend)
    graph = create_deep_agent(
        model=model,
        backend=backend,
        checkpointer=MemorySaver(),
        middleware=[summarization],
    )

    result = await graph.ainvoke(
        {
            "messages": [
                HumanMessage(id="u1", content="Earlier context. " * 15000),
                AIMessage(id="a1", content="second"),
                HumanMessage(id="u2", content="third"),
            ]
        },
        {"configurable": {"thread_id": "same-thread"}},
    )

    snapshot = await graph.aget_state({"configurable": {"thread_id": "same-thread"}})
    event = snapshot.values["_summarization_event"]
    summary_message = event["summary_message"]
    assert summary_message.additional_kwargs[SUMMARY_MARKER_KEY]["summary"] == "Compressed context."
    archive = summary_message.additional_kwargs[SUMMARY_MARKER_KEY]["archive_path"]
    assert archive in snapshot.values["files"]
    assert "/conversation_history/" in summary_message.content
    assert "`/conversations/`" in summary_message.content
    assert result["messages"][-1].content == "Continued."


def test_summary_wrapper_without_archive_never_claims_retrieval_and_retains_body() -> None:
    middleware = ProgenaSummarizationMiddleware(
        model=ProviderChatModel(responses=[]), backend=StateBackend()
    )

    [message] = middleware._build_new_messages_with_path("Exact body.", None)

    assert "/conversation_history/" not in message.content
    assert "/conversations/" not in message.content
    assert summary_body(message) == "Exact body."


def test_no_archive_wrapper_uses_only_available_canonical_conversation_mount() -> None:
    backend = CompositeBackend(default=StateBackend(), routes={"/conversations/": StateBackend()})
    middleware = ProgenaSummarizationMiddleware(
        model=ProviderChatModel(responses=[]), backend=backend
    )

    [message] = middleware._build_new_messages_with_path("Exact body.", None)

    assert "/conversation_history/" not in message.content
    assert "`/conversations/`" in message.content
