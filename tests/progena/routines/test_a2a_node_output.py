"""A2A node output validation through LangGraph's native retry budget."""

from __future__ import annotations

import asyncio
import copy
from typing import Any
from unittest.mock import Mock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from src.progena.a2a.payloads import classify_a2a_reply
from src.progena.registry.models import AgentRegistryEntry
from src.progena.registry.store import ToolRegistry
from src.progena.routines.factory import compile_routine_graph
from src.progena.routines.graph import RoutineGraph, validate_routine_graph_payload
from src.progena.routines.node_output import NodeOutputRetryError

SEND = "src.progena.a2a.client.A2AClient.send_message"
ORIGINAL_PROMPT = "Review draft {doc} and return your verdict"
VALID = '{"verdict": "approve", "score": 7}'
SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["verdict", "score"],
    "properties": {
        "verdict": {"enum": ["approve", "revise"]},
        "score": {"type": "integer", "minimum": 0, "maximum": 10},
    },
}


def _task(
    text: str,
    *,
    state: str = "completed",
    context_id: str | None = "ctx-1",
    task_id: str = "task-1",
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "kind": "task",
        "id": task_id,
        "status": {
            "state": state,
            "message": {
                "kind": "message",
                "role": "agent",
                "messageId": f"reply-{task_id}",
                "parts": [{"kind": "text", "text": text}],
            },
        },
        # Never let a caller-authored task history become reply text.
        "history": [{"role": "user", "parts": [{"kind": "text", "text": "caller text"}]}],
    }
    if context_id is not None:
        result["contextId"] = context_id
    return {"success": True, "result": result}


class Peer:
    def __init__(self, *responses: Any) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, agent_config: Any, request: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append({"agent_config": agent_config, "request": request, **kwargs})
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def _registry() -> Mock:
    return Mock(find_by_name=Mock(return_value={"transport": "http", "url": "http://peer.test"}))


def _config(**extra: Any) -> dict[str, Any]:
    return {
        "agent_name": "reviewer",
        "prompt_template": ORIGINAL_PROMPT,
        "response_schema": copy.deepcopy(SCHEMA),
        "retry": {"max_retries": 0},
        **extra,
    }


def _retry(max_retries: int, retry_on: str = "all", **extra: Any) -> dict[str, Any]:
    return {"max_retries": max_retries, "initial_interval": 0, "retry_on": retry_on, **extra}


def _graph(config: dict[str, Any]) -> RoutineGraph:
    return validate_routine_graph_payload(
        {
            "id": "a2a-node-output",
            "name": "a2a-node-output",
            "description": "",
            "version": "2.0.0",
            "entry": "call",
            "nodes": [{"id": "call", "kind": "a2a", "output_key": "review", "config": config}],
            "edges": [],
        }
    )


class Model:
    async def ainvoke(self, messages, *args, **kwargs):
        return AIMessage(content="unused")


def _runnable(config: dict[str, Any], *, tool_registry: Any | None = None, **kwargs: Any):
    return compile_routine_graph(
        _graph(config),
        model=Model(),
        tool_registry=tool_registry or _registry(),
        **kwargs,
    )


def _input(doc: str = "D-1") -> dict[str, Any]:
    return {"messages": [HumanMessage(content="go")], "data": {"doc": doc}}


async def test_invalid_completed_reply_uses_one_native_feedback_retry() -> None:
    peer = Peer(_task("not JSON"), _task(VALID, task_id="task-2"))
    runnable = _runnable(_config(retry=_retry(1)))

    with patch(SEND, new=peer):
        result = await runnable.ainvoke(_input())

    assert len(peer.calls) == 2
    assert peer.calls[0]["request"] == "Review draft D-1 and return your verdict"
    assert peer.calls[1]["context_id"] == "ctx-1"
    assert peer.calls[1]["task_id"] is None
    assert "Review draft" not in peer.calls[1]["request"]
    assert "not JSON" not in peer.calls[1]["request"]
    assert result["data"]["review"] == VALID
    assert result["data"]["review_json"] == {"verdict": "approve", "score": 7}
    record = result["run_meta"]["call"][0]["node_output"]
    assert [(attempt["kind"], attempt["valid"]) for attempt in record["attempts"]] == [
        ("original", False),
        ("feedback", True),
    ]


async def test_transport_and_validation_share_the_native_retry_budget() -> None:
    peer = Peer(
        {"success": False, "error": "connection reset"},
        _task("not JSON"),
        _task(VALID, task_id="task-2"),
    )
    runnable = _runnable(_config(retry=_retry(2)))

    with patch(SEND, new=peer):
        result = await runnable.ainvoke(_input())

    assert len(peer.calls) == 3
    original_requests = [
        call["request"] == "Review draft D-1 and return your verdict" for call in peer.calls
    ]
    assert original_requests == [True, True, False]
    assert peer.calls[-1]["context_id"] == "ctx-1"
    assert result["data"]["review_json"]["score"] == 7


async def test_noncompleted_task_keeps_ordinary_original_retry() -> None:
    peer = Peer(_task("", state="working"), _task(VALID))
    runnable = _runnable(_config(retry=_retry(1)))

    with patch(SEND, new=peer):
        result = await runnable.ainvoke(_input())

    assert len(peer.calls) == 2
    assert all(call["request"] == "Review draft D-1 and return your verdict" for call in peer.calls)
    assert result["data"]["review_json"]["score"] == 7


async def test_completed_reply_without_a_context_fails_without_replay() -> None:
    peer = Peer(_task("not JSON", context_id=None))
    runnable = _runnable(_config(retry=_retry(3)))

    with patch(SEND, new=peer), pytest.raises(NodeOutputRetryError) as exc:
        await runnable.ainvoke(_input())

    assert exc.value.code == "no_protocol_context"
    assert len(peer.calls) == 1


async def test_feedback_transport_failure_is_terminal_and_never_replays_original() -> None:
    peer = Peer(_task("not JSON"), {"success": False, "error": "unknown context"})
    runnable = _runnable(_config(retry=_retry(3)))

    with patch(SEND, new=peer), pytest.raises(NodeOutputRetryError) as exc:
        await runnable.ainvoke(_input())

    assert exc.value.code == "feedback_send_failed"
    assert len(peer.calls) == 2
    assert peer.calls[0]["request"] == "Review draft D-1 and return your verdict"
    assert peer.calls[1]["context_id"] == "ctx-1"


async def test_feedback_stops_if_same_named_peer_is_reregistered() -> None:
    old_url = "https://old-private-peer.example/a2a"
    new_url = "https://new-private-peer.example/a2a"
    registry = ToolRegistry()

    def entry(url: str) -> AgentRegistryEntry:
        return AgentRegistryEntry(
            name="reviewer",
            transport="http",
            url=url,
            endpoint=url,
            headers={"Authorization": "Bearer private-token"},
        )

    await registry.register_agent(entry(old_url))

    class ReplacingPeer:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def __call__(self, agent_config: Any, request: str, **kwargs: Any) -> dict[str, Any]:
            self.calls.append({"agent_config": agent_config, "request": request, **kwargs})
            if len(self.calls) == 1:
                await registry.register_agent(entry(new_url))
                return _task("not JSON")
            raise AssertionError("feedback must not reach a replacement peer")

    peer = ReplacingPeer()
    runnable = _runnable(_config(retry=_retry(3)), tool_registry=registry)

    with patch(SEND, new=peer), pytest.raises(NodeOutputRetryError) as exc:
        await runnable.ainvoke(_input())

    assert exc.value.code == "feedback_peer_changed"
    assert len(peer.calls) == 1
    record = exc.value.node_output
    assert isinstance(record, dict)
    assert len(record["peer_fingerprint"]) == 64
    persisted = repr(record)
    for secret in (old_url, new_url, "private-token"):
        assert secret not in persisted


async def test_cancellation_during_feedback_propagates() -> None:
    calls: list[dict[str, Any]] = []
    feedback_started = asyncio.Event()

    async def send(_client: Any, agent_config: Any, request: str, **kwargs: Any) -> dict[str, Any]:
        calls.append({"request": request, **kwargs})
        if len(calls) == 1:
            return _task("not JSON")
        feedback_started.set()
        await asyncio.Event().wait()
        raise AssertionError("feedback wait must be cancelled")

    runnable = _runnable(_config(retry=_retry(3)))
    with patch(SEND, new=send):
        task = asyncio.create_task(runnable.ainvoke(_input()))
        try:
            await asyncio.wait_for(feedback_started.wait(), timeout=5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    assert len(calls) == 2
    assert calls[0]["request"] == "Review draft D-1 and return your verdict"
    assert calls[1]["context_id"] == "ctx-1"


async def test_completed_task_with_only_caller_history_is_a_validation_failure() -> None:
    response = _task("", context_id="ctx-1")
    response["result"]["status"].pop("message")
    peer = Peer(response, _task(VALID, task_id="task-2"))
    runnable = _runnable(_config(retry=_retry(1, "model_response")))

    with patch(SEND, new=peer):
        result = await runnable.ainvoke(_input())

    assert len(peer.calls) == 2
    assert peer.calls[1]["context_id"] == "ctx-1"
    assert "caller text" not in peer.calls[1]["request"]
    assert result["data"]["review_json"]["score"] == 7


async def test_concurrent_runs_keep_feedback_contexts_isolated() -> None:
    class ContextPeer:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def __call__(self, agent_config: Any, request: str, **kwargs: Any) -> dict[str, Any]:
            self.calls.append({"request": request, **kwargs})
            if "Top-level schema constraints:" in request:
                context_id = kwargs["context_id"]
                return _task(VALID, context_id=context_id, task_id=f"reply-{context_id}")
            doc = "D-1" if "D-1" in request else "D-2"
            return _task("not JSON", context_id=f"ctx-{doc}", task_id=f"initial-{doc}")

    peer = ContextPeer()
    runnable = _runnable(_config(retry=_retry(1)))
    with patch(SEND, new=peer):
        first, second = await asyncio.gather(
            runnable.ainvoke(_input("D-1")),
            runnable.ainvoke(_input("D-2")),
        )

    feedback_calls = [call for call in peer.calls if "context_id" in call]
    assert {call["context_id"] for call in feedback_calls} == {"ctx-D-1", "ctx-D-2"}
    assert first["data"]["review_json"]["score"] == 7
    assert second["data"]["review_json"]["score"] == 7


@pytest.mark.parametrize("timeout_kind", ["run", "idle"])
async def test_native_timeout_after_feedback_never_replays_the_original(
    timeout_kind: str,
) -> None:
    from langgraph.errors import NodeTimeoutError

    from src.progena.settings.policies import PolicyValues

    class SlowFeedbackPeer:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def __call__(self, agent_config: Any, request: str, **kwargs: Any) -> dict[str, Any]:
            self.calls.append({"request": request, **kwargs})
            if len(self.calls) == 1:
                return _task("not JSON")
            await asyncio.sleep(0.5)
            return _task(VALID)

    peer = SlowFeedbackPeer()
    config = _config(
        retry=_retry(2, jitter=False),
        **({"timeout": 0.01} if timeout_kind == "run" else {}),
    )
    runnable_options = (
        {"execution_policy": PolicyValues(idle_timeout=0.01)} if timeout_kind == "idle" else {}
    )
    runnable = _runnable(config, **runnable_options)

    with patch(SEND, new=peer), pytest.raises(NodeTimeoutError):
        await runnable.ainvoke(_input())

    assert len(peer.calls) >= 2
    assert peer.calls[0]["request"] == "Review draft D-1 and return your verdict"
    assert all(
        call["request"] != "Review draft D-1 and return your verdict" for call in peer.calls[1:]
    )
    assert all(call["context_id"] == "ctx-1" for call in peer.calls[1:])


def test_classification_uses_only_peer_text_parts_not_task_history() -> None:
    payload = _task("")["result"]
    payload["artifacts"] = [{"parts": [{"kind": "text", "text": VALID}]}]

    reply = classify_a2a_reply(payload)

    assert reply.completed
    assert reply.text == VALID


async def test_schema_free_a2a_node_keeps_lenient_raw_and_json_outputs() -> None:
    peer = Peer(_task('Sure! ```json\n{"verdict": "approve"}\n```'))
    config = _config()
    config.pop("response_schema")
    runnable = _runnable(config)

    with patch(SEND, new=peer):
        result = await runnable.ainvoke(_input())

    assert result["data"]["review"] == 'Sure! ```json\n{"verdict": "approve"}\n```'
    assert result["data"]["review_json"] == {"verdict": "approve"}
    assert "node_output" not in result["run_meta"]["call"][0]
