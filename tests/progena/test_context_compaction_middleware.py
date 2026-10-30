from __future__ import annotations

import pytest
from deepagents.backends import FilesystemBackend
from deepagents.middleware.summarization import SummarizationMiddleware
from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver

from src.progena.agent.middleware.context_compaction import ContextCompactionMiddleware
from src.progena.agent.state import ProgenaAgentState
from src.progena.sessions.messages import extract_context_usage, get_last_ai_message


def _provider_message(
    message_id: str,
    input_tokens: int,
    *,
    provider_id: str = "provider-1",
    deployment: str = "model",
) -> AIMessage:
    return AIMessage(
        id=message_id,
        content="response",
        usage_metadata={
            "input_tokens": input_tokens,
            "output_tokens": 10,
            "total_tokens": input_tokens + 10,
        },
        response_metadata={"insights": {"provider_id": provider_id, "deployment": deployment}},
    )


class _BoundFakeMessagesListChatModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        del tools, kwargs
        return self


@pytest.mark.asyncio
async def test_native_summary_carries_descriptor_into_later_root_provider_call(tmp_path) -> None:
    model = _BoundFakeMessagesListChatModel(
        responses=[
            _provider_message("post-summary", 400),
            _provider_message("later-root", 450),
        ]
    )
    saver = MemorySaver()
    graph = create_agent(
        model,
        middleware=[
            SummarizationMiddleware(
                model=_BoundFakeMessagesListChatModel(responses=[AIMessage(content="summary")]),
                backend=FilesystemBackend(root_dir=tmp_path, virtual_mode=True),
                trigger=("messages", 5),
                keep=("messages", 1),
            ),
            ContextCompactionMiddleware(),
        ],
        state_schema=ProgenaAgentState,
        checkpointer=saver,
    )
    config = {"configurable": {"thread_id": "native-compaction-thread"}}
    historical = [
        HumanMessage(content="one"),
        _provider_message("before-summary", 1_200),
        HumanMessage(content="two"),
        HumanMessage(content="three"),
        HumanMessage(content="four"),
    ]

    await graph.ainvoke({"messages": historical}, config)
    first_snapshot = await graph.aget_state(config)
    assert extract_context_usage(get_last_ai_message(first_snapshot.values["messages"]))[
        "compaction"
    ] == {
        "before_input_tokens": 1_200,
        "after_input_tokens": 400,
        "freed_input_tokens": 800,
        "measurement": "comparable_provider_footprints",
    }

    await graph.ainvoke({"messages": [HumanMessage(content="next")]}, config)
    reloaded = await graph.aget_state(config)
    assert extract_context_usage(get_last_ai_message(reloaded.values["messages"]))[
        "compaction"
    ] == {
        "before_input_tokens": 1_200,
        "after_input_tokens": 400,
        "freed_input_tokens": 800,
        "measurement": "comparable_provider_footprints",
    }


@pytest.mark.asyncio
async def test_compaction_does_not_fabricate_savings_when_comparable_footprint_grows() -> None:
    middleware = ContextCompactionMiddleware()
    before = _provider_message("before", 400)
    after = _provider_message("after", 600)
    update = await middleware.aafter_model(
        {
            "messages": [before, HumanMessage(content="continue"), after],
            "_summarization_event": {
                "created_at_index": 2,
                "cutoff_index": 1,
                "summary_message": HumanMessage(content="summary"),
            },
        },
        runtime=None,
    )

    assert update == {"_context_compaction": None}


@pytest.mark.asyncio
async def test_compaction_rejects_cross_model_comparison_and_clears_old_descriptor() -> None:
    middleware = ContextCompactionMiddleware()
    before = _provider_message("before", 1_200, deployment="old-model")
    after = _provider_message("after", 400, deployment="new-model")
    after.response_metadata["progena_context_compaction"] = {
        "before_input_tokens": 2_000,
        "after_input_tokens": 1_000,
        "freed_input_tokens": 1_000,
        "measurement": "comparable_provider_footprints",
    }

    update = await middleware.aafter_model(
        {
            "messages": [before, HumanMessage(content="continue"), after],
            "_summarization_event": {
                "created_at_index": 2,
                "cutoff_index": 1,
                "summary_message": HumanMessage(content="summary"),
            },
        },
        runtime=None,
    )

    assert update == {
        "_context_compaction": None,
        "messages": [_provider_message("after", 400, deployment="new-model")],
    }


@pytest.mark.asyncio
async def test_later_provider_response_carries_persisted_latest_compaction_descriptor() -> None:
    middleware = ContextCompactionMiddleware()
    later = _provider_message("later", 500)
    state = {
        "_context_compaction": {
            "before_input_tokens": 1_200,
            "after_input_tokens": 400,
            "freed_input_tokens": 800,
            "measurement": "comparable_provider_footprints",
        }
    }

    class Request:
        def __init__(self) -> None:
            self.state = state

    async def handler(_request):
        from langchain.agents.middleware.types import ModelResponse

        return ModelResponse(result=[later])

    response = await middleware.awrap_model_call(Request(), handler)

    assert extract_context_usage(response.result[0])["compaction"] == {
        "before_input_tokens": 1_200,
        "after_input_tokens": 400,
        "freed_input_tokens": 800,
        "measurement": "comparable_provider_footprints",
    }
