"""Call attribution must survive LangChain chunk merging and graph checkpoints."""

import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    HumanMessage,
    SystemMessage,
    ToolMessage,
    message_to_dict,
    messages_from_dict,
)
from langchain_core.outputs import ChatGenerationChunk
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph

import src.progena.agent.checkpoint_usage as checkpoint_usage
from src.progena.agent.checkpoint_usage import ChatGPTResponseStreamError, CheckpointChatOpenAI


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


def _responses_model(
    *,
    provider_id: str | None,
    provider_kind: str = "foundry",
    base_url: str = "https://foundry.example.test/openai/v1",
    **kwargs,
) -> CheckpointChatOpenAI:
    model = CheckpointChatOpenAI(
        model="test-model",
        api_key="local-test",
        use_responses_api=True,
        output_version="responses/v1",
        **kwargs,
    )
    model.set_provider_connection(
        provider_id=provider_id,
        provider_kind=provider_kind,
        base_url=base_url,
    )
    model.set_provider_kind(provider_kind)
    return model


def _history_with_reasoning(origin: dict[str, str] | None) -> list:
    response_metadata = {"id": "resp_foundry"}
    if origin is not None:
        response_metadata["progena_provider"] = origin
    return [
        HumanMessage(content="Find the answer"),
        AIMessage(
            content=[
                {
                    "type": "reasoning",
                    "id": "rs_foundry",
                    "encrypted_content": "encrypted-for-foundry",
                    "summary": [{"type": "summary_text", "text": "private reasoning"}],
                },
                {"type": "output_text", "id": "msg_foundry", "text": "I will look it up."},
                {
                    "type": "function_call",
                    "call_id": "call_lookup",
                    "name": "lookup",
                    "arguments": '{"query":"answer"}',
                },
            ],
            response_metadata=response_metadata,
        ),
        ToolMessage(content="The answer is 42", tool_call_id="call_lookup"),
        HumanMessage(content="What did you find?"),
    ]


def _response_items(payload: dict) -> list[dict]:
    return payload["input"]


def test_same_connection_replays_encrypted_reasoning() -> None:
    origin = {
        "id": "foundry-connection",
        "kind": "foundry",
        "base_url": "https://foundry.example.test/openai/v1",
    }

    payload = _responses_model(provider_id="foundry-connection")._get_request_payload(
        _history_with_reasoning(origin)
    )

    items = _response_items(payload)
    reasoning = next(item for item in items if item["type"] == "reasoning")
    assert reasoning["encrypted_content"] == "encrypted-for-foundry"
    assert any(
        item["type"] == "function_call" and item["call_id"] == "call_lookup" for item in items
    )
    assert any(
        item["type"] == "function_call_output" and item["output"] == "The answer is 42"
        for item in items
    )


def test_legacy_kind_and_base_url_attribution_replays_encrypted_reasoning() -> None:
    origin = {
        "kind": "foundry",
        "base_url": "https://foundry.example.test/openai/v1",
    }

    payload = _responses_model(provider_id=None)._get_request_payload(
        _history_with_reasoning(origin)
    )

    assert any(item["type"] == "reasoning" for item in _response_items(payload))


def test_chatgpt_reasoning_replay_is_scoped_to_its_origin_connection() -> None:
    origin = {
        "id": "chatgpt-connection",
        "kind": "chatgpt",
        "base_url": "https://api.openai.com/v1",
    }
    history = _history_with_reasoning(origin)

    same_payload = _responses_model(
        provider_id="chatgpt-connection",
        provider_kind="chatgpt",
        base_url="https://api.openai.com/v1",
    )._get_request_payload(history)
    different_payload = _responses_model(
        provider_id="other-chatgpt-connection",
        provider_kind="chatgpt",
        base_url="https://api.openai.com/v1",
    )._get_request_payload(history)

    assert any(item["type"] == "reasoning" for item in _response_items(same_payload))
    assert not any(item["type"] == "reasoning" for item in _response_items(different_payload))


def test_cross_connection_strips_encrypted_reasoning_but_keeps_visible_history() -> None:
    origin = {
        "id": "foundry-connection",
        "kind": "foundry",
        "base_url": "https://foundry.example.test/openai/v1",
    }

    payload = _responses_model(
        provider_id="go-connection",
        provider_kind="opencode_go",
        base_url="https://opencode.ai/inference/go/openai/v1",
    )._get_request_payload(_history_with_reasoning(origin))

    items = _response_items(payload)
    assert not any(item["type"] == "reasoning" for item in items)
    assert any(
        item["type"] == "message"
        and item["role"] == "assistant"
        and item["content"][0]["text"] == "I will look it up."
        for item in items
    )
    assert any(
        item["type"] == "function_call" and item["call_id"] == "call_lookup" for item in items
    )
    assert any(
        item["type"] == "function_call_output" and item["output"] == "The answer is 42"
        for item in items
    )


def test_old_checkpoint_without_connection_attribution_strips_opaque_history() -> None:
    [legacy_message] = messages_from_dict([message_to_dict(_history_with_reasoning(None)[1])])
    history = [
        HumanMessage(content="Find the answer"),
        legacy_message,
        ToolMessage(content="The answer is 42", tool_call_id="call_lookup"),
        HumanMessage(content="What did you find?"),
    ]

    payload = _responses_model(
        provider_id="go-connection",
        provider_kind="opencode_go",
        base_url="https://opencode.ai/inference/go/openai/v1",
    )._get_request_payload(history)

    items = _response_items(payload)
    assert not any(item["type"] == "reasoning" for item in items)
    assert any(item["type"] == "function_call" for item in items)


def test_previous_response_id_is_only_replayed_to_its_origin_connection() -> None:
    origin = {
        "id": "foundry-connection",
        "kind": "foundry",
        "base_url": "https://foundry.example.test/openai/v1",
    }
    history = _history_with_reasoning(origin)

    same_payload = _responses_model(
        provider_id="foundry-connection", use_previous_response_id=True
    )._get_request_payload(history)
    cross_payload = _responses_model(
        provider_id="go-connection",
        provider_kind="opencode_go",
        base_url="https://opencode.ai/inference/go/openai/v1",
        use_previous_response_id=True,
    )._get_request_payload(history)

    assert same_payload["previous_response_id"] == "resp_foundry"
    assert "previous_response_id" not in cross_payload
    assert any(
        item["type"] == "message" and item.get("role") == "assistant"
        for item in cross_payload["input"]
    )


async def test_cross_provider_request_smoke_does_not_send_encrypted_reasoning() -> None:
    requests: list[dict] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "resp_go",
                "object": "response",
                "created_at": 0,
                "status": "completed",
                "model": "grok-4.7",
                "error": None,
                "output": [
                    {
                        "id": "msg_go",
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "output_text", "text": "42", "annotations": []}],
                    }
                ],
            },
        )

    origin = {
        "id": "foundry-connection",
        "kind": "foundry",
        "base_url": "https://foundry.example.test/openai/v1",
    }
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        model = _responses_model(
            provider_id="go-connection",
            provider_kind="opencode_go",
            base_url="https://opencode.ai/inference/go/openai/v1",
            streaming=False,
            http_async_client=client,
        )
        response = await model.ainvoke(_history_with_reasoning(origin))

    assert response.text == "42"
    assert len(requests) == 1
    request_items = requests[0]["input"]
    assert not any(item["type"] == "reasoning" for item in request_items)
    assert any(item["type"] == "function_call" for item in request_items)


def test_chatgpt_request_payload_uses_stateless_streaming_and_namespaced_tools():
    model = CheckpointChatOpenAI(
        model="gpt-test",
        api_key="oauth-token",
        use_responses_api=True,
        temperature=0.2,
        max_tokens=12,
        extra_body={"reasoning": {"effort": "medium", "summary": "detailed"}},
    )
    model.set_provider_kind("chatgpt")

    payload = model._get_request_payload(
        [SystemMessage(content="Follow the contract."), HumanMessage(content="Use the tool.")],
        stream=False,
        tools=[
            {
                "type": "function",
                "name": "lookup",
                "description": "Look up a record.",
                "parameters": {"type": "object", "properties": {}},
            }
        ],
    )

    assert payload["store"] is False
    assert payload["stream"] is True
    assert payload["include"] == ["reasoning.encrypted_content"]
    assert payload["input"][0]["role"] == "developer"
    assert {"temperature", "max_output_tokens", "previous_response_id"}.isdisjoint(payload)
    assert payload["reasoning"] == {"effort": "medium", "summary": "detailed"}
    namespace = payload["tools"][0]
    assert namespace["type"] == "namespace"
    assert namespace["name"] == "progena"
    assert namespace["tools"][0]["type"] == "function"
    assert namespace["tools"][0]["name"] == "lookup"
    assert model._usage_context()["provider"] == "chatgpt"


def test_chatgpt_function_call_keeps_separate_namespace_for_replay():
    chunk = ChatGenerationChunk(
        message=AIMessageChunk(
            content=[
                {
                    "type": "function_call",
                    "name": "lookup",
                    "call_id": "call_1",
                    "arguments": "{}",
                }
            ]
        )
    )

    CheckpointChatOpenAI._preserve_chatgpt_tool_namespace(
        SimpleNamespace(item=SimpleNamespace(namespace="progena")),
        chunk,
    )

    assert chunk.message.content == [
        {
            "type": "function_call",
            "name": "lookup",
            "namespace": "progena",
            "call_id": "call_1",
            "arguments": "{}",
        }
    ]


@pytest.mark.asyncio
async def test_chatgpt_ainvoke_uses_responses_stream_and_replays_final_reasoning():
    requests: list[httpx.Request] = []
    reasoning_started = {
        "type": "response.output_item.added",
        "sequence_number": 1,
        "output_index": 0,
        "item": {
            "id": "rs_1",
            "type": "reasoning",
            "summary": [],
            "status": "in_progress",
        },
    }
    reasoning_summary_added = {
        "type": "response.reasoning_summary_part.added",
        "sequence_number": 2,
        "output_index": 0,
        "item_id": "rs_1",
        "summary_index": 0,
    }
    reasoning_summary_delta = {
        "type": "response.reasoning_summary_text.delta",
        "sequence_number": 3,
        "output_index": 0,
        "item_id": "rs_1",
        "summary_index": 0,
        "delta": "Checked the request.",
    }
    reasoning_done = {
        "type": "response.output_item.done",
        "sequence_number": 4,
        "output_index": 0,
        "item": {
            "id": "rs_1",
            "type": "reasoning",
            "summary": [],
            "status": "completed",
            "encrypted_content": "opaque-reasoning",
        },
    }
    completed = {
        "type": "response.completed",
        "sequence_number": 5,
        "response": {
            "id": "resp_1",
            "object": "response",
            "created_at": 0,
            "status": "completed",
            "model": "gpt-test",
            "error": None,
            "output": [reasoning_done["item"]],
            "parallel_tool_calls": True,
            "tool_choice": "auto",
            "tools": [],
        },
    }

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        frames = (
            reasoning_started,
            reasoning_summary_added,
            reasoning_summary_delta,
            reasoning_done,
            completed,
        )
        text = "".join(f"event: {frame['type']}\ndata: {json.dumps(frame)}\n\n" for frame in frames)
        return httpx.Response(200, text=text, headers={"content-type": "text/event-stream"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        model = CheckpointChatOpenAI(
            model="gpt-test",
            base_url="https://api.openai.com/v1",
            api_key="oauth-token",
            use_responses_api=True,
            output_version="responses/v1",
            streaming=True,
            http_async_client=client,
        )
        model.set_provider_kind("chatgpt")
        model.set_provider_connection(
            provider_id="chatgpt-connection",
            provider_kind="chatgpt",
            base_url="https://api.openai.com/v1",
        )
        message = await model.ainvoke(
            [SystemMessage(content="Follow policy."), HumanMessage(content="Hi")]
        )

    assert requests[0].url.path == "/v1/responses"
    payload = json.loads(requests[0].content)
    assert payload["store"] is False
    assert payload["stream"] is True
    assert payload["input"][0]["role"] == "developer"
    replay = model._get_request_payload([message])["input"]
    assert replay == [
        {
            "id": "rs_1",
            "type": "reasoning",
            "summary": [{"type": "summary_text", "text": "Checked the request."}],
            "status": "completed",
            "encrypted_content": "opaque-reasoning",
        }
    ]


@pytest.mark.asyncio
async def test_chatgpt_langchain_stream_retries_unauthorized_before_emitting_chunks(monkeypatch):
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(401, json={"error": {"message": "expired"}})
        completed = {
            "type": "response.completed",
            "response": {
                "id": "resp_2",
                "object": "response",
                "created_at": 0,
                "status": "completed",
                "model": "gpt-test",
                "error": None,
                "output": [
                    {
                        "id": "msg_1",
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "output_text", "text": "retried"}],
                    }
                ],
                "parallel_tool_calls": True,
                "tool_choice": "auto",
                "tools": [],
            },
        }
        return httpx.Response(
            200,
            text=f"event: response.completed\ndata: {json.dumps(completed)}\n\n",
            headers={"content-type": "text/event-stream"},
        )

    refresh = AsyncMock(return_value="rotated-token")
    monkeypatch.setattr(
        checkpoint_usage,
        "refresh_chatgpt_access_token_after_rejection",
        refresh,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        model = CheckpointChatOpenAI(
            model="gpt-test",
            base_url="https://api.openai.com/v1",
            api_key="rejected-token",
            use_responses_api=True,
            output_version="responses/v1",
            streaming=True,
            http_async_client=client,
        )
        model.set_provider_kind("chatgpt")
        model.set_chatgpt_provider("provider-1", "rejected-token")
        await model.ainvoke([HumanMessage(content="Retry.")])

    assert [request.headers["authorization"] for request in requests] == [
        "Bearer rejected-token",
        "Bearer rotated-token",
    ]
    refresh.assert_awaited_once_with("provider-1", "rejected-token")


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["response.failed", "response.incomplete", None])
async def test_chatgpt_normal_ainvoke_rejects_failed_or_unfinished_streams(terminal):
    events = [
        {
            "type": "response.output_text.delta",
            "sequence_number": 0,
            "output_index": 0,
            "content_index": 0,
            "item_id": "msg_partial",
            "delta": "A partial answer",
        }
    ]
    if terminal:
        events.append(
            {
                "type": terminal,
                "sequence_number": 1,
                "response": {
                    "id": "resp_failed",
                    "status": terminal.split(".")[1],
                    "error": {"code": "usage_limit_reached", "message": "Plan limit"},
                },
            }
        )

    def respond(request):
        text = "".join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events)
        return httpx.Response(200, text=text, headers={"content-type": "text/event-stream"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        model = CheckpointChatOpenAI(
            model="gpt-test",
            api_key="synthetic-test-token",
            use_responses_api=True,
            output_version="responses/v1",
            streaming=True,
            http_async_client=client,
        )
        model.set_provider_kind("chatgpt")
        with pytest.raises(ChatGPTResponseStreamError):
            await model.ainvoke([HumanMessage(content="Hello")])
