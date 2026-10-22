"""Call attribution must survive LangChain chunk merging and graph checkpoints."""

import json
from datetime import datetime

import httpx
import pytest
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph

from src.progenitor.agent.checkpoint_usage import CheckpointChatOpenAI


@pytest.mark.parametrize("streaming", [False, True])
async def test_profile_identity_survives_checkpointed_model_turns(streaming):
    calls = 0

    def respond(request):
        nonlocal calls
        calls += 1
        body = json.loads(request.content)
        identity = f"chatcmpl-{calls}"
        usage = {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15}
        if not body.get("stream"):
            return httpx.Response(
                200,
                json={
                    "id": identity,
                    "object": "chat.completion",
                    "created": 1,
                    "model": "shared-deployment",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "Hello world"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": usage,
                },
            )
        chunks = [
            {
                "id": identity,
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "shared-deployment",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"role": "assistant", "content": "Hello "},
                        "finish_reason": None,
                    }
                ],
            },
            {
                "id": identity,
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "shared-deployment",
                "choices": [{"index": 0, "delta": {"content": "world"}, "finish_reason": "stop"}],
            },
            {
                "id": identity,
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "shared-deployment",
                "choices": [],
                "usage": usage,
            },
        ]
        text = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks) + "data: [DONE]\n\n"
        return httpx.Response(200, text=text, headers={"content-type": "text/event-stream"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        model = CheckpointChatOpenAI(
            model="shared-deployment",
            api_key="local-test",
            use_responses_api=False,
            streaming=streaming,
            stream_usage=True,
            http_async_client=client,
        )
        model.set_usage_profile("review")

        async def reply(state):
            return {"messages": [await model.ainvoke(state["messages"])]}

        builder = StateGraph(MessagesState)
        builder.add_node("reply", reply)
        builder.add_edge(START, "reply")
        builder.add_edge("reply", END)
        graph = builder.compile(checkpointer=InMemorySaver())
        config = {"configurable": {"thread_id": "usage-attribution"}}
        await graph.ainvoke({"messages": [HumanMessage(content="First")]}, config)
        first = (await graph.aget_state(config)).values["messages"][-1]
        model.set_usage_profile("planning")
        await graph.ainvoke({"messages": [HumanMessage(content="Second")]}, config)
        saved = (await graph.aget_state(config)).values["messages"]

    assistant_messages = [message for message in saved if message.type == "ai"]
    assert [message.content for message in assistant_messages] == ["Hello world", "Hello world"]
    assert [
        message.response_metadata["insights"]["profile_name"] for message in assistant_messages
    ] == ["review", "planning"]
    assert [message.usage_metadata["total_tokens"] for message in assistant_messages] == [15, 15]
    assert len({message.id for message in assistant_messages}) == 2
    for message in assistant_messages:
        context = message.response_metadata["insights"]
        assert context["deployment"] == "shared-deployment"
        assert datetime.fromisoformat(context["occurred_at"]).tzinfo is not None
    assert first.response_metadata["insights"]["profile_name"] == "review"
    assert calls == 2
