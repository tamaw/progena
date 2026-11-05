"""Regression coverage for model-node response-schema feedback retries."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_openai import ChatOpenAI

from src.progena.routines.factory import compile_routine_graph
from src.progena.routines.graph import validate_routine_graph_payload
from src.progena.routines.node_output import OutputSchema
from src.progena.settings.policies import PolicyValues

_SCHEMA = {
    "type": "object",
    "properties": {"result": {"type": "boolean"}},
    "required": ["result"],
    "additionalProperties": False,
}
_RETRY = {"max_retries": 1, "initial_interval": 0, "jitter": False, "retry_on": "model_response"}


class _Model:
    def __init__(self) -> None:
        self.normal_calls: list[str] = []
        self.structured_calls: list[list[Any]] = []
        self.bind_calls: list[dict[str, Any]] = []

    async def ainvoke(self, messages: list[Any]) -> AIMessage:
        prompt = next(message.content for message in messages if isinstance(message, HumanMessage))
        self.normal_calls.append(prompt)
        return AIMessage(content=prompt)

    def with_structured_output(self, schema: dict[str, Any], **kwargs: Any):
        self.bind_calls.append({"schema": schema, **kwargs})
        outer = self

        class _StructuredRunnable:
            async def ainvoke(self, messages: list[Any]) -> dict[str, Any]:
                outer.structured_calls.append(messages)
                prompt = next(
                    message.content for message in messages if isinstance(message, HumanMessage)
                )
                has_feedback = any(isinstance(message, AIMessage) for message in messages)
                if not has_feedback:
                    # Simulate a provider producing a parsed, but locally invalid,
                    # structured result with no text content in its raw message.
                    return {
                        "raw": AIMessage(content=""),
                        "parsed": {"result": f"bad-{prompt}"},
                        "parsing_error": ValueError("provider included raw response"),
                    }
                return {
                    "raw": AIMessage(content=json.dumps({"result": True})),
                    "parsed": {"result": True},
                    "parsing_error": None,
                }

        return _StructuredRunnable()


class _ParsingFailureModel(_Model):
    def with_structured_output(self, schema: dict[str, Any], **kwargs: Any):
        self.bind_calls.append({"schema": schema, **kwargs})
        outer = self

        class _StructuredRunnable:
            async def ainvoke(self, messages: list[Any]) -> dict[str, Any]:
                outer.structured_calls.append(messages)
                if len(outer.structured_calls) == 1:
                    return {
                        "raw": AIMessage(content='{"result": true}'),
                        "parsed": None,
                        "parsing_error": ValueError("untrusted provider parser detail"),
                    }
                return {
                    "raw": AIMessage(content='{"result": true}'),
                    "parsed": {"result": True},
                    "parsing_error": None,
                }

        return _StructuredRunnable()


def _graph(
    *,
    kind: str = "prompt",
    surrounding_nodes: bool = False,
    model_profile: str | None = None,
) -> Any:
    if kind == "prompt":
        config: dict[str, Any] = {
            "prompt_template": "step {job}",
            "response_schema": _SCHEMA,
            "retry": _RETRY,
        }
    else:
        config = {
            "rubric_prompt": "step {job}",
            "response_schema": _SCHEMA,
            "retry": _RETRY,
        }
    if model_profile is not None:
        config["model_profile"] = model_profile
    nodes: list[dict[str, Any]] = [
        {"id": "step", "kind": kind, "output_key": "answer", "config": config}
    ]
    edges: list[dict[str, Any]] = []
    entry = "step"
    if surrounding_nodes:
        nodes = [
            {
                "id": "before",
                "kind": "prompt",
                "output_key": "before",
                "config": {"prompt_template": "before", "retry": {"max_retries": 0}},
            },
            *nodes,
            {
                "id": "after",
                "kind": "prompt",
                "output_key": "after",
                "config": {"prompt_template": "after", "retry": {"max_retries": 0}},
            },
        ]
        edges = [
            {"from": "before", "to": "step", "level": "normal"},
            {"from": "step", "to": "after", "level": "normal"},
        ]
        entry = "before"
    return validate_routine_graph_payload(
        {
            "id": f"{kind}-output-feedback",
            "name": "output feedback",
            "description": "",
            "version": "2.0.0",
            "entry": entry,
            "nodes": nodes,
            "edges": edges,
        }
    )


def _root_ref_graph() -> Any:
    return validate_routine_graph_payload(
        {
            "id": "root-ref-output-feedback",
            "name": "root ref output feedback",
            "description": "",
            "version": "2.0.0",
            "entry": "step",
            "nodes": [
                {
                    "id": "step",
                    "kind": "prompt",
                    "output_key": "answer",
                    "config": {
                        "prompt_template": "step",
                        "response_schema": {"$ref": "#/$defs/result"},
                        "retry": _RETRY,
                    },
                }
            ],
            "edges": [],
            "$defs": {"result": _SCHEMA},
        }
    )


@pytest.mark.parametrize("kind", ["prompt", "classify"])
async def test_model_nodes_preserve_structured_output_shape_after_feedback_retry(kind: str) -> None:
    model = _Model()
    result = await compile_routine_graph(_graph(kind=kind), model=model).ainvoke(
        {"data": {"job": "shape"}}
    )

    assert result["data"]["answer"] == {"result": True}
    assert len(model.structured_calls) == 2


async def test_provider_parsing_failure_retries_even_when_raw_reply_looks_valid() -> None:
    model = _ParsingFailureModel()
    result = await compile_routine_graph(_graph(), model=model).ainvoke(
        {"data": {"job": "provider-parse"}}
    )

    assert result["data"]["answer"] == {"result": True}
    assert len(model.structured_calls) == 2
    retry_messages = model.structured_calls[1]
    assert any(
        message.content == '{"result": true}'
        for message in retry_messages
        if isinstance(message, AIMessage)
    )
    correction = [
        message.content for message in retry_messages if isinstance(message, HumanMessage)
    ][-1]
    assert "provider_parse_failed" in correction
    assert "untrusted provider parser detail" not in correction


async def test_retry_uses_feedback_without_replaying_neighbor_nodes() -> None:
    model = _Model()
    result = await compile_routine_graph(_graph(surrounding_nodes=True), model=model).ainvoke(
        {"data": {"job": "one"}}
    )

    assert result["data"]["answer"] == {"result": True}
    assert model.normal_calls == ["before", "after"]
    assert len(model.structured_calls) == 2
    retry_messages = model.structured_calls[1]
    rejected = next(message for message in retry_messages if isinstance(message, AIMessage))
    correction = [
        message.content for message in retry_messages if isinstance(message, HumanMessage)
    ][-1]
    assert rejected.content == '{"result": "bad-step one"}'
    assert "wrong_type" in correction
    assert "bad-step one" not in correction


async def test_concurrent_native_retries_keep_feedback_in_the_matching_task() -> None:
    model = _Model()
    compiled = compile_routine_graph(_graph(), model=model)

    results = await asyncio.gather(
        compiled.ainvoke({"data": {"job": "alpha"}}),
        compiled.ainvoke({"data": {"job": "beta"}}),
    )

    assert [result["data"]["answer"] for result in results] == [
        {"result": True},
        {"result": True},
    ]
    assert len(model.structured_calls) == 4
    initial_calls = [
        messages
        for messages in model.structured_calls
        if not any(isinstance(message, AIMessage) for message in messages)
    ]
    retry_calls = [
        messages
        for messages in model.structured_calls
        if any(isinstance(message, AIMessage) for message in messages)
    ]
    assert len(initial_calls) == len(retry_calls) == 2
    for messages in retry_calls:
        original_prompt = next(
            message.content for message in messages if isinstance(message, HumanMessage)
        )
        rejected = next(message for message in messages if isinstance(message, AIMessage))
        correction = [message.content for message in messages if isinstance(message, HumanMessage)][
            -1
        ]
        assert rejected.content == json.dumps({"result": f"bad-{original_prompt}"})
        assert "wrong_type" in correction
        other_prompt = "step beta" if original_prompt == "step alpha" else "step alpha"
        assert f"bad-{other_prompt}" not in rejected.content


@pytest.mark.parametrize(
    ("node_profile", "expected_profile"),
    [(True, "configured"), (False, "inherited")],
)
async def test_classify_profile_retries_with_feedback(
    node_profile: bool, expected_profile: str
) -> None:
    model = _Model()
    resolved: list[str] = []

    async def resolve(profile: str) -> _Model:
        resolved.append(profile)
        return model

    result = await compile_routine_graph(
        _graph(kind="classify", model_profile="configured" if node_profile else None),
        model=_Model(),
        model_profile_resolver=resolve,
        execution_policy=PolicyValues(default_model_profile=None if node_profile else "inherited"),
    ).ainvoke({"data": {"job": "profile"}})

    assert result["data"]["answer"] == {"result": True}
    assert resolved == [expected_profile, expected_profile]
    assert len(model.structured_calls) == 2


def test_provider_schema_leaves_literal_ref_data_untouched() -> None:
    schema = OutputSchema(
        {
            "type": "object",
            "properties": {
                "$ref": {
                    "const": {"$ref": "literal-data"},
                }
            },
        }
    )

    assert schema.provider_schema()["properties"]["$ref"]["const"] == {"$ref": "literal-data"}


def _chat_completion(content: str, *, identifier: str) -> dict[str, Any]:
    return {
        "id": identifier,
        "object": "chat.completion",
        "created": 1,
        "model": "gpt-4o-2024-08-06",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


def _tool_call_completion(arguments: str, *, identifier: str, tool_name: str) -> dict[str, Any]:
    return {
        "id": identifier,
        "object": "chat.completion",
        "created": 1,
        "model": "gpt-3.5-turbo",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": f"call-{identifier}",
                            "type": "function",
                            "function": {"name": tool_name, "arguments": arguments},
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
    }


def _requested_tool_name(payload: dict[str, Any]) -> str:
    choice = payload.get("tool_choice")
    if isinstance(choice, str):
        return choice
    if isinstance(choice, dict):
        function = choice.get("function")
        name = function.get("name") if isinstance(function, dict) else None
        if isinstance(name, str):
            return name
    return payload["tools"][0]["function"]["name"]


async def test_root_ref_schema_retries_through_chat_openai_with_feedback() -> None:
    requests: list[dict[str, Any]] = []
    replies = iter(
        [
            _chat_completion('{"result": "incorrect"}', identifier="chatcmpl-first"),
            _chat_completion('{"result": true}', identifier="chatcmpl-second"),
        ]
    )

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=next(replies))

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        model = ChatOpenAI(
            model="gpt-4o-2024-08-06",
            api_key="test-key",
            base_url="https://openai.test/v1",
            http_async_client=client,
        )
        result = await compile_routine_graph(_root_ref_graph(), model=model).ainvoke({"data": {}})

    assert result["data"]["answer"] == {"result": True}
    assert len(requests) == 2
    assert (
        requests[0]["response_format"]["json_schema"]["schema"]["properties"]
        == _SCHEMA["properties"]
    )
    retry_messages = requests[1]["messages"]
    assert any(
        message["role"] == "assistant" and message["content"] == '{"result": "incorrect"}'
        for message in retry_messages
    )
    assert "wrong_type" in json.dumps(retry_messages)


async def test_tool_call_arguments_become_plain_feedback_assistant_content() -> None:
    requests: list[dict[str, Any]] = []
    malformed_arguments = '{"result": broken}'

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append(payload)
        tool_name = _requested_tool_name(payload)
        arguments = malformed_arguments if len(requests) == 1 else '{"result": true}'
        return httpx.Response(
            200,
            json=_tool_call_completion(
                arguments,
                identifier=f"chatcmpl-tool-{len(requests)}",
                tool_name=tool_name,
            ),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        model = ChatOpenAI(
            model="gpt-3.5-turbo",
            api_key="test-key",
            base_url="https://openai.test/v1",
            http_async_client=client,
        )
        result = await compile_routine_graph(_graph(), model=model).ainvoke(
            {"data": {"job": "tool-call"}}
        )

    assert result["data"]["answer"] == {"result": True}
    assert len(requests) == 2
    retry_messages = requests[1]["messages"]
    rejected = next(message for message in retry_messages if message["role"] == "assistant")
    assert rejected["content"] == malformed_arguments
    assert "malformed_json" in retry_messages[-1]["content"]
    assert "tool_calls" not in rejected
