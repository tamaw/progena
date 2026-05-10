#!/usr/bin/env python3
"""Run repeatable live evaluations against a running Progenitor instance."""

from __future__ import annotations

import argparse
import json
import re
import socket
import struct
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

DEFAULT_IPC_HOST = "127.0.0.1"
DEFAULT_IPC_PORT = 9500
HEADER_SIZE = 4
ENCODING = "utf-8"
ASYNC_SUBAGENT_RESULT = "HELLO_FROM_ASYNC_SUBAGENT"


@dataclass
class EvaluationResult:
    name: str
    passed: bool
    expectation: str
    observed: str
    details: dict[str, Any] = field(default_factory=dict)
    root_cause_area: str | None = None


def _write_ipc(sock: socket.socket, payload: dict[str, Any]) -> None:
    data = json.dumps(payload).encode(ENCODING)
    sock.sendall(struct.pack(">I", len(data)) + data)


def _read_ipc(sock: socket.socket) -> dict[str, Any] | None:
    header = _recv_exact(sock, HEADER_SIZE)
    if not header:
        return None
    (length,) = struct.unpack(">I", header)
    payload = _recv_exact(sock, length)
    if not payload:
        return None
    return json.loads(payload.decode(ENCODING))


def _recv_exact(sock: socket.socket, size: int) -> bytes | None:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = sock.recv(remaining)
        if not chunk:
            return None
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _open_ipc(host: str, port: int, timeout: float) -> socket.socket:
    sock = socket.create_connection((host, port), timeout=timeout)
    sock.settimeout(timeout)
    ready = _read_ipc(sock)
    if not ready or ready.get("type") != "ready":
        sock.close()
        raise RuntimeError(f"IPC server did not send ready message: {ready!r}")
    return sock


def _ipc_assistant_response(
    host: str,
    port: int,
    timeout: float,
    prompt: str,
    *,
    metadata: dict[str, Any] | None = None,
) -> str:
    with _open_ipc(host, port, timeout) as sock:
        _write_ipc(
            sock,
            {
                "type": "user_message",
                "content": prompt,
                "request_id": f"live-eval-{uuid.uuid4().hex[:8]}",
                "metadata": metadata or {"source": "live_evaluation"},
            },
        )
        while True:
            msg = _read_ipc(sock)
            if msg is None:
                raise RuntimeError("connection closed before assistant response")
            msg_type = msg.get("type")
            if msg_type == "agent_event":
                continue
            if msg_type == "error":
                raise RuntimeError(str(msg.get("content") or "unknown IPC error"))
            if msg_type == "assistant_message":
                return str(msg.get("content") or "")
            raise RuntimeError(f"unexpected IPC message type: {msg_type}")


def _ipc_stream_items(
    host: str,
    port: int,
    timeout: float,
    prompt: str,
    *,
    metadata: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    with _open_ipc(host, port, timeout) as sock:
        _write_ipc(
            sock,
            {
                "type": "user_message",
                "content": prompt,
                "stream": True,
                "request_id": f"live-eval-stream-{uuid.uuid4().hex[:8]}",
                "metadata": metadata or {"source": "live_evaluation"},
            },
        )
        while True:
            msg = _read_ipc(sock)
            if msg is None:
                raise RuntimeError("connection closed before final stream item")
            items.append(msg)
            msg_type = msg.get("type")
            if msg_type == "error":
                raise RuntimeError(str(msg.get("content") or "unknown IPC error"))
            if msg_type == "stream_chunk" and msg.get("is_final") is True:
                return items
            if msg_type in {"stream_end", "assistant_message"}:
                return items


def _extract_task_id(text: str) -> str | None:
    match = re.search(r"(?:TASK_ID=|task_id[:=]\s*)([A-Za-z0-9_\-]+)", text)
    return match.group(1) if match else None


def _pass(name: str, expectation: str, observed: str, **details: Any) -> EvaluationResult:
    return EvaluationResult(name, True, expectation, observed, details)


def _fail(
    name: str,
    expectation: str,
    observed: str,
    *,
    root_cause_area: str,
    **details: Any,
) -> EvaluationResult:
    return EvaluationResult(name, False, expectation, observed, details, root_cause_area)


def eval_ipc_heartbeat(host: str, port: int, timeout: float) -> EvaluationResult:
    name = "ipc_ready_and_heartbeat"
    expectation = "IPC accepts TCP connection, sends ready, and replies heartbeat_ack to heartbeat."
    try:
        with _open_ipc(host, port, timeout) as sock:
            _write_ipc(sock, {"type": "heartbeat", "content": ""})
            msg = _read_ipc(sock)
    except Exception as exc:
        return _fail(
            name, expectation, f"IPC heartbeat failed: {exc}", root_cause_area="ipc/server.py"
        )

    if msg and msg.get("type") == "heartbeat_ack":
        return _pass(name, expectation, "IPC ready and heartbeat ack observed")
    return _fail(
        name,
        expectation,
        f"unexpected IPC heartbeat response: {msg}",
        root_cause_area="ipc/server.py",
        message=msg,
    )


def eval_ipc_non_streaming(host: str, port: int, timeout: float) -> EvaluationResult:
    marker = f"LIVE_EVAL_OK_{uuid.uuid4().hex[:8]}"
    name = "ipc_non_streaming_llm_turn"
    expectation = f"Non-streaming IPC user message returns assistant_message containing {marker}."
    prompt = f"Reply with exactly this token and no other text: {marker}"
    try:
        content = _ipc_assistant_response(
            host,
            port,
            timeout,
            prompt,
            metadata={"source": "live_evaluation", "mode": "non_streaming"},
        )
        if marker in content:
            return _pass(name, expectation, "assistant_message contained expected marker")
        return _fail(
            name,
            expectation,
            f"assistant response did not contain marker: {content[:300]!r}",
            root_cause_area="agent/adapters.py or live LLM configuration",
        )
    except Exception as exc:
        return _fail(
            name,
            expectation,
            f"non-streaming IPC turn failed: {exc}",
            root_cause_area="ipc/server.py or agent/adapters.py",
        )


def eval_ipc_streaming(host: str, port: int, timeout: float) -> EvaluationResult:
    marker = f"LIVE_EVAL_STREAM_OK_{uuid.uuid4().hex[:8]}"
    name = "ipc_streaming_llm_turn"
    expectation = (
        f"Streaming IPC returns chunks ending with is_final=true and content containing {marker}."
    )
    prompt = f"Reply with exactly this token and no other text: {marker}"
    chunks: list[str] = []
    final_seen = False
    event_types: list[str] = []
    try:
        for msg in _ipc_stream_items(
            host,
            port,
            timeout,
            prompt,
            metadata={"source": "live_evaluation", "mode": "streaming"},
        ):
            msg_type = msg.get("type")
            if msg_type == "agent_event":
                continue
            if msg_type == "stream_event":
                event_types.append(str(msg.get("event_type") or "unknown"))
                continue
            if msg_type == "stream_chunk":
                chunks.append(str(msg.get("content") or ""))
                if msg.get("is_final") is True:
                    final_seen = True
            elif msg_type in {"stream_end", "assistant_message"}:
                if msg_type == "assistant_message":
                    chunks.append(str(msg.get("content") or ""))
                final_seen = True
    except Exception as exc:
        return _fail(
            name,
            expectation,
            f"streaming IPC turn failed: {exc}",
            root_cause_area="ipc/server.py or ipc/deepagent_stream.py",
        )

    combined = "".join(chunks)
    if final_seen and marker in combined:
        return _pass(
            name,
            expectation,
            "stream chunks contained marker and final signal",
            chunk_count=len(chunks),
            stream_events=event_types,
        )
    return _fail(
        name,
        expectation,
        f"stream did not meet expectation: final={final_seen}, content={combined[:300]!r}",
        root_cause_area="ipc/deepagent_stream.py or live LLM configuration",
        chunk_count=len(chunks),
        stream_events=event_types,
    )


def eval_reasoning_stream(host: str, port: int, timeout: float) -> EvaluationResult:
    name = "ipc_reasoning_stream"
    expectation = (
        "With reasoning enabled and summary=auto, at least one reasoning-heavy streaming "
        "prompt emits a reasoning_delta stream_event before the final answer."
    )
    prompts = [
        "Think carefully about 217 * 384, verify once, then answer exactly PRODUCT=83328.",
        "Work out 37*49 + 18*23 carefully and answer exactly RESULT=2227.",
        (
            "Solve this step by step: 14 workers need 18 days to finish a job at constant rate. "
            "How many days would 21 workers need? End with DAYS=12."
        ),
    ]
    attempts: list[dict[str, Any]] = []
    for prompt_index, prompt in enumerate(prompts, start=1):
        for attempt in range(1, 3):
            try:
                items = _ipc_stream_items(
                    host,
                    port,
                    timeout,
                    prompt,
                    metadata={
                        "source": "live_evaluation",
                        "mode": "reasoning_stream",
                        "prompt_index": prompt_index,
                        "attempt": attempt,
                    },
                )
            except Exception as exc:
                attempts.append(
                    {
                        "prompt_index": prompt_index,
                        "attempt": attempt,
                        "error": str(exc),
                    }
                )
                continue

            reasoning_events = [
                item
                for item in items
                if item.get("type") == "stream_event"
                and item.get("event_type") == "reasoning_delta"
            ]
            combined = "".join(
                str(item.get("content") or "")
                for item in items
                if item.get("type") == "stream_chunk"
            )
            attempts.append(
                {
                    "prompt_index": prompt_index,
                    "attempt": attempt,
                    "reasoning_event_count": len(reasoning_events),
                    "final_text": combined[:200],
                }
            )
            if reasoning_events:
                return _pass(
                    name,
                    expectation,
                    "reasoning_delta events were observed during streaming",
                    prompt_index=prompt_index,
                    attempt=attempt,
                    reasoning_event_count=len(reasoning_events),
                    final_text=combined,
                )
    return _fail(
        name,
        expectation,
        "no reasoning_delta events observed across the reasoning prompt corpus",
        root_cause_area="ipc/deepagent_stream.py or live model/provider reasoning behavior",
        attempts=attempts,
    )


def eval_tool_catalog(host: str, port: int, timeout: float) -> EvaluationResult:
    name = "tool_catalog_via_chat"
    expectation = (
        "Via list_tools, the live agent exposes schedule_task, self_reflect, new_conversation, "
        "get_a2a_task, and call_agent."
    )
    prompt = (
        "Use the list_tools tool. Reply with only a comma-separated list of the available system "
        "tool names."
    )
    try:
        response = _ipc_assistant_response(
            host,
            port,
            timeout,
            prompt,
            metadata={"source": "live_evaluation", "mode": "tool_catalog"},
        )
    except Exception as exc:
        return _fail(
            name,
            expectation,
            f"tool catalog request failed: {exc}",
            root_cause_area="tools/list_tools.py or agent tool wiring",
        )
    required = ["schedule_task", "self_reflect", "new_conversation", "get_a2a_task", "call_agent"]
    missing = [tool for tool in required if tool not in response]
    if not missing:
        return _pass(name, expectation, "required system tools were listed", response=response)
    return _fail(
        name,
        expectation,
        f"missing tools from response: {missing}; response={response[:300]!r}",
        root_cause_area="tools/list_tools.py or agent tool wiring",
    )


def eval_microsoft_learn_mcp(host: str, port: int, timeout: float) -> EvaluationResult:
    name = "microsoft_learn_mcp_via_chat"
    expectation = (
        "The live agent can refresh MCP discovery, expose the Microsoft Learn MCP tools, "
        "and call microsoft_learn_microsoft_docs_search from chat."
    )
    check_prompt = (
        "Use the mcp_refresh tool, then use list_tools, and tell me whether these MCP tools are available: "
        "microsoft_learn_microsoft_docs_search, "
        "microsoft_learn_microsoft_code_sample_search, "
        "microsoft_learn_microsoft_docs_fetch."
    )
    try:
        check_response = _ipc_assistant_response(
            host,
            port,
            timeout,
            check_prompt,
            metadata={"source": "live_evaluation", "mode": "mcp_catalog"},
        )
    except Exception as exc:
        return _fail(
            name,
            expectation,
            f"Microsoft Learn MCP tool discovery failed: {exc}",
            root_cause_area="tools/mcp_refresh.py, tools/list_tools.py, or MCP runtime wiring",
        )

    required_tools = [
        "microsoft_learn_microsoft_docs_search",
        "microsoft_learn_microsoft_code_sample_search",
        "microsoft_learn_microsoft_docs_fetch",
    ]
    missing = [tool for tool in required_tools if tool not in check_response]
    if missing:
        return _fail(
            name,
            expectation,
            f"Microsoft Learn MCP tools were not all visible: missing={missing}; response={check_response[:300]!r}",
            root_cause_area="tools/mcp_refresh.py, tools/list_tools.py, or MCP config loading",
        )

    tool_prompt = (
        "Use the microsoft_learn_microsoft_docs_search MCP tool to find official Microsoft Learn guidance "
        "for Azure Blob Storage lifecycle management. Return only two lines in this format:\n"
        "TITLE=<title>\nURL=<url>"
    )
    try:
        items = _ipc_stream_items(
            host,
            port,
            max(timeout, 120.0),
            tool_prompt,
            metadata={"source": "live_evaluation", "mode": "microsoft_learn_mcp"},
        )
    except Exception as exc:
        return _fail(
            name,
            expectation,
            f"Microsoft Learn MCP tool call failed: {exc}",
            root_cause_area="MCP runtime wiring, ipc/deepagent_stream.py, or Microsoft Learn MCP availability",
        )

    tool_events = [
        item
        for item in items
        if item.get("type") == "stream_event"
        and item.get("event_type") in {"tool_start", "tool_result"}
        and "microsoft_learn_microsoft_docs_search" in str(item.get("content") or "")
    ]
    combined = "".join(
        str(item.get("content") or "") for item in items if item.get("type") == "stream_chunk"
    )
    if (
        len(tool_events) >= 2
        and "TITLE=" in combined
        and "URL=https://learn.microsoft.com/" in combined
    ):
        return _pass(
            name,
            expectation,
            "Microsoft Learn MCP tools were exposed and microsoft_docs_search was called successfully",
            catalog_response=check_response,
            tool_event_types=[str(item.get("event_type") or "") for item in tool_events],
            response=combined,
        )
    return _fail(
        name,
        expectation,
        (
            "Microsoft Learn MCP discovery succeeded but the live tool call did not produce the expected "
            f"tool events/output: response={combined[:300]!r}"
        ),
        root_cause_area="MCP runtime wiring, ipc/deepagent_stream.py, or Microsoft Learn MCP behavior",
        catalog_response=check_response,
        tool_events=tool_events,
        response=combined,
    )


def eval_subagent_delegation(host: str, port: int, timeout: float) -> EvaluationResult:
    name = "subagent_delegation_via_chat"
    expectation = (
        "A streaming chat request can delegate to the hello-world subagent, emit subagent_start and "
        "subagent_result events, and return HELLO_FROM_SUBAGENT to the user."
    )
    prompt = (
        "Use the hello-world subagent for this request. "
        "Reply with exactly HELLO_FROM_SUBAGENT and nothing else."
    )
    try:
        items = _ipc_stream_items(
            host,
            port,
            timeout,
            prompt,
            metadata={"source": "live_evaluation", "mode": "subagent_delegation"},
        )
    except Exception as exc:
        return _fail(
            name,
            expectation,
            f"subagent delegation request failed: {exc}",
            root_cause_area="agent/agent.py, agent/subagents.py, or ipc/deepagent_stream.py",
        )

    event_types = [
        str(item.get("event_type") or "") for item in items if item.get("type") == "stream_event"
    ]
    final_text = "".join(
        str(item.get("content") or "")
        for item in items
        if item.get("type") in {"stream_chunk", "assistant_message"}
    )
    if (
        "subagent_start" in event_types
        and "subagent_result" in event_types
        and "HELLO_FROM_SUBAGENT" in final_text
    ):
        return _pass(
            name,
            expectation,
            "hello-world subagent delegated and returned HELLO_FROM_SUBAGENT",
            stream_events=event_types,
            final_text=final_text,
        )
    return _fail(
        name,
        expectation,
        "subagent delegation did not surface the expected events and final answer",
        root_cause_area="agent/subagents.py, agent/agent.py, or ipc/stream_events.py",
        stream_events=event_types,
        final_text=final_text[:500],
    )


def eval_async_subagent_background_completion(
    host: str,
    port: int,
    timeout: float,
) -> EvaluationResult:
    name = "async_subagent_background_completion"
    expectation = (
        "A chat request can launch hello-world-async via start_async_task, surface async kickoff "
        "telemetry, return a task id immediately, and later receive a heartbeat-driven IPC callback "
        f"with {ASYNC_SUBAGENT_RESULT}."
    )
    prompt = (
        "Use the start_async_task tool with subagent_type hello-world-async. "
        f"description should say to return exactly {ASYNC_SUBAGENT_RESULT}. "
        "Do not call check_async_task, list_async_tasks, update_async_task, or cancel_async_task. "
        "Reply with exactly TASK_ID=<task_id>."
    )
    request_id = f"live-eval-async-{uuid.uuid4().hex[:8]}"
    try:
        with _open_ipc(host, port, max(timeout, 120.0)) as sock:
            _write_ipc(
                sock,
                {
                    "type": "user_message",
                    "content": prompt,
                    "stream": True,
                    "request_id": request_id,
                    "metadata": {"source": "live_evaluation", "mode": "async_subagent"},
                },
            )
            kickoff_items: list[dict[str, Any]] = []
            while True:
                msg = _read_ipc(sock)
                if msg is None:
                    raise RuntimeError("connection closed before async-subagent kickoff completed")
                kickoff_items.append(msg)
                msg_type = msg.get("type")
                if msg_type == "error":
                    raise RuntimeError(str(msg.get("content") or "unknown IPC error"))
                if msg_type == "stream_chunk" and msg.get("is_final") is True:
                    break
                if msg_type in {"stream_end", "assistant_message"}:
                    break

            stream_events = [
                str(item.get("event_type") or "")
                for item in kickoff_items
                if item.get("type") == "stream_event"
            ]
            final_text = "".join(
                str(item.get("content") or "")
                for item in kickoff_items
                if item.get("type") in {"stream_chunk", "assistant_message"}
            )
            task_id = _extract_task_id(final_text)
            if not task_id:
                for item in kickoff_items:
                    metadata = item.get("metadata")
                    if not isinstance(metadata, dict):
                        continue
                    task_id = _extract_task_id(json.dumps(metadata, default=str))
                    if task_id:
                        break
            if not task_id:
                raise RuntimeError(f"could not extract async task id from kickoff: {final_text!r}")

            callback: dict[str, Any] | None = None
            deadline = time.time() + 45.0
            while time.time() < deadline:
                remaining = max(1.0, deadline - time.time())
                sock.settimeout(min(remaining, 5.0))
                try:
                    msg = _read_ipc(sock)
                except TimeoutError:
                    continue
                if msg is None or msg.get("type") != "agent_event":
                    continue
                if str(msg.get("event_type") or "") != "agent_message":
                    continue
                content = str(msg.get("content") or "")
                metadata = msg.get("metadata") if isinstance(msg.get("metadata"), dict) else {}
                serialized = json.dumps(metadata, default=str)
                if (
                    ASYNC_SUBAGENT_RESULT in content
                    and task_id in serialized
                    and metadata.get("source") == "async_subagent_completion"
                ):
                    callback = msg
                    break
    except Exception as exc:
        return _fail(
            name,
            expectation,
            f"async subagent live eval failed: {exc}",
            root_cause_area=(
                "agent/subagents.py, agent/middleware.py, async_subagents/, or heartbeat async-subagent nodes"
            ),
        )

    if (
        "async_subagent_start" in stream_events
        and f"TASK_ID={task_id}" in final_text
        and callback is not None
    ):
        return _pass(
            name,
            expectation,
            "async subagent launched, returned a task id, and later emitted a heartbeat IPC callback",
            task_id=task_id,
            stream_events=stream_events,
            final_text=final_text,
            callback=callback,
        )
    return _fail(
        name,
        expectation,
        (
            "async subagent kickoff/callback did not meet expectations: "
            f"stream_events={stream_events}, final_text={final_text[:300]!r}, callback={callback}"
        ),
        root_cause_area=(
            "agent/middleware.py, async_subagents/, runtime/http.py, or heartbeat async-subagent nodes"
        ),
        task_id=task_id,
        stream_events=stream_events,
        final_text=final_text,
        callback=callback,
    )


def eval_self_reflect_tool(host: str, port: int, timeout: float) -> EvaluationResult:
    name = "self_reflect_via_chat"
    expectation = "The live agent can use self_reflect to inspect its codebase."
    prompt = (
        "Use the self_reflect tool with file_path config.py to read that file. If that succeeds, reply with exactly "
        "FOUND_CONFIG."
    )
    try:
        response = _ipc_assistant_response(
            host,
            port,
            timeout,
            prompt,
            metadata={"source": "live_evaluation", "mode": "self_reflect"},
        )
    except Exception as exc:
        return _fail(
            name,
            expectation,
            f"self_reflect request failed: {exc}",
            root_cause_area="tools/self_reflect.py or tool wiring",
        )
    if "FOUND_CONFIG" in response:
        return _pass(name, expectation, "agent used self_reflect successfully")
    return _fail(
        name,
        expectation,
        f"unexpected self_reflect response: {response[:300]!r}",
        root_cause_area="tools/self_reflect.py or tool wiring",
    )


def eval_conversation_history(host: str, port: int, timeout: float) -> tuple[EvaluationResult, str]:
    token = "marzipan"
    name = "conversation_history"
    expectation = (
        "The current thread remembers the previous user message and can recall a token from it."
    )
    try:
        first = _ipc_assistant_response(
            host,
            port,
            timeout,
            f"For this conversation only, the code word is {token}.",
            metadata={"source": "live_evaluation", "mode": "conversation_history_start"},
        )
        second = _ipc_assistant_response(
            host,
            port,
            timeout,
            "What is the conversation-only code word? Reply with one word only.",
            metadata={"source": "live_evaluation", "mode": "conversation_history_check"},
        )
    except Exception as exc:
        return (
            _fail(
                name,
                expectation,
                f"conversation history request failed: {exc}",
                root_cause_area="nodes/cycle.py or conversation state persistence",
            ),
            token,
        )
    if first.strip() and second.strip().lower() == token:
        return _pass(
            name, expectation, "agent recalled the prior-message token", token=token
        ), token
    return (
        _fail(
            name,
            expectation,
            f"unexpected memory of prior turn: first={first[:120]!r}, second={second[:200]!r}",
            root_cause_area="conversation graph/checkpointer/state persistence",
        ),
        token,
    )


def eval_new_conversation_reset(
    host: str,
    port: int,
    timeout: float,
    prior_token: str,
) -> EvaluationResult:
    name = "new_conversation_reset"
    expectation = (
        "new_conversation starts a fresh conversation so the old token is no longer available."
    )
    try:
        _ipc_assistant_response(
            host,
            port,
            timeout,
            "new conversation",
            metadata={"source": "live_evaluation", "mode": "new_conversation"},
        )
        recall = _ipc_assistant_response(
            host,
            port,
            timeout,
            "What is the conversation-only code word? If you do not know, reply with exactly UNKNOWN.",
            metadata={"source": "live_evaluation", "mode": "new_conversation_check"},
        )
    except Exception as exc:
        return _fail(
            name,
            expectation,
            f"new_conversation eval failed: {exc}",
            root_cause_area="tools/new_conversation.py or nodes/cycle.py",
        )

    normalized = recall.strip().lower()
    if normalized == "unknown" or prior_token not in normalized:
        return _pass(name, expectation, "post-reset conversation did not expose the prior token")
    return _fail(
        name,
        expectation,
        f"agent still appeared to know the prior token after reset: {recall[:200]!r}",
        root_cause_area="tools/new_conversation.py or conversation state reset handling",
    )


def eval_schedule_create_and_trigger(
    host: str,
    port: int,
    timeout: float,
) -> EvaluationResult:
    name = "schedule_create_and_trigger"
    token = f"SCHEDULE_TOKEN_{uuid.uuid4().hex[:8]}"
    expectation = (
        "A one-off scheduled task can be created via chat, confirmed via list_schedule, "
        "and later trigger scheduled_response/work_completed IPC events."
    )
    scheduled_at = time.strftime(
        "%Y-%m-%dT%H:%M:%SZ",
        time.gmtime(time.time() + 45),
    )
    prompt = (
        "Use the schedule_task tool now. "
        f"user_request should be 'say {token}'. "
        f"instruction should be 'Reply exactly {token}.'. "
        f"scheduled_at should be '{scheduled_at}'. "
        f"description should be 'live eval {token}'."
    )
    try:
        response_text = _ipc_assistant_response(
            host,
            port,
            max(timeout, 120.0),
            prompt,
            metadata={"source": "live_evaluation", "mode": "schedule"},
        )
        schedule_listing = ""
        for _ in range(12):
            schedule_listing = _ipc_assistant_response(
                host,
                port,
                timeout,
                "Use the list_schedule tool and reply with the raw schedule list only.",
                metadata={"source": "live_evaluation", "mode": "schedule_check"},
            )
            if token in schedule_listing:
                break
            time.sleep(1.0)
        else:
            raise RuntimeError(
                f"list_schedule did not expose the scheduled token: {schedule_listing!r}"
            )

        with _open_ipc(host, port, max(timeout, 120.0)) as sock:
            observed_events: list[str] = []
            deadline = time.time() + 100.0
            while time.time() < deadline:
                remaining = max(1.0, deadline - time.time())
                sock.settimeout(min(remaining, 5.0))
                try:
                    msg = _read_ipc(sock)
                except TimeoutError:
                    continue
                if msg is None:
                    continue
                if msg.get("type") != "agent_event":
                    continue
                event_type = str(msg.get("event_type") or "")
                content = str(msg.get("content") or "")
                metadata = msg.get("metadata") or {}
                if token in content or token in json.dumps(metadata):
                    observed_events.append(event_type)
                if {"scheduled_response", "work_completed"} <= set(observed_events):
                    break
    except Exception as exc:
        return _fail(
            name,
            expectation,
            f"schedule live eval failed: {exc}",
            root_cause_area="tools/schedule_task.py, schedule/dispatch.py, or heartbeat graph scheduler path",
        )

    if {"scheduled_response", "work_completed"} <= set(observed_events):
        return _pass(
            name,
            expectation,
            "schedule was persisted and later emitted scheduled completion events",
            response=response_text,
            observed_events=observed_events,
            token=token,
        )
    return _fail(
        name,
        expectation,
        f"schedule created but did not emit expected events; observed={observed_events}",
        root_cause_area="schedule/dispatch.py or heartbeat scheduler loop",
        response=response_text,
        token=token,
    )


def eval_memory_backend_write(host: str, port: int, timeout: float) -> EvaluationResult:
    name = "memory_backend_write"
    expectation = (
        "The live agent can write and immediately read durable memory content under /memories/."
    )
    token = f"MEMORY_TOKEN_{uuid.uuid4().hex[:8]}"
    prompt = (
        f"Write the exact text '{token}' into /memories/live-eval-memory.md, then read that file back, "
        f"and reply with exactly {token}."
    )
    try:
        response = _ipc_assistant_response(
            host,
            port,
            timeout,
            prompt,
            metadata={"source": "live_evaluation", "mode": "memory_write"},
        )
    except Exception as exc:
        return _fail(
            name,
            expectation,
            f"memory write request failed: {exc}",
            root_cause_area="agent/backends.py or backend file tools",
        )
    if response.strip() == token:
        return _pass(name, expectation, "memory file write/read round-trip succeeded")
    return _fail(
        name,
        expectation,
        f"memory round-trip did not return the expected token: {response[:200]!r}",
        root_cause_area="agent/backends.py or backend file tools",
    )


def run(args: argparse.Namespace) -> list[EvaluationResult]:
    results = [eval_ipc_heartbeat(args.ipc_host, args.ipc_port, args.ipc_timeout)]
    results.append(eval_tool_catalog(args.ipc_host, args.ipc_port, args.llm_timeout))
    results.append(eval_microsoft_learn_mcp(args.ipc_host, args.ipc_port, args.llm_timeout))
    results.append(eval_subagent_delegation(args.ipc_host, args.ipc_port, args.llm_timeout))
    results.append(
        eval_async_subagent_background_completion(
            args.ipc_host,
            args.ipc_port,
            args.llm_timeout,
        )
    )
    history_result, history_token = eval_conversation_history(
        args.ipc_host, args.ipc_port, args.llm_timeout
    )
    results.append(history_result)
    results.append(
        eval_new_conversation_reset(
            args.ipc_host,
            args.ipc_port,
            args.llm_timeout,
            history_token,
        )
    )
    results.append(eval_reasoning_stream(args.ipc_host, args.ipc_port, args.llm_timeout))
    if not args.skip_llm:
        results.append(eval_ipc_non_streaming(args.ipc_host, args.ipc_port, args.llm_timeout))
        results.append(eval_ipc_streaming(args.ipc_host, args.ipc_port, args.llm_timeout))
        if not args.skip_schedule:
            results.append(
                eval_schedule_create_and_trigger(
                    args.ipc_host,
                    args.ipc_port,
                    args.llm_timeout,
                )
            )
        if not args.skip_memory_write:
            results.append(
                eval_memory_backend_write(args.ipc_host, args.ipc_port, args.llm_timeout)
            )
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ipc-host", default=DEFAULT_IPC_HOST)
    parser.add_argument("--ipc-port", type=int, default=DEFAULT_IPC_PORT)
    parser.add_argument("--ipc-timeout", type=float, default=10.0)
    parser.add_argument("--llm-timeout", type=float, default=240.0)
    parser.add_argument("--skip-llm", action="store_true")
    parser.add_argument("--skip-schedule", action="store_true")
    parser.add_argument("--skip-memory-write", action="store_true")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON results.")
    args = parser.parse_args()

    results = run(args)
    payload = {
        "target": {
            "ipc": f"{args.ipc_host}:{args.ipc_port}",
        },
        "results": [asdict(result) for result in results],
        "summary": {
            "passed": sum(1 for result in results if result.passed),
            "failed": sum(1 for result in results if not result.passed),
            "total": len(results),
        },
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        for result in results:
            marker = "PASS" if result.passed else "FAIL"
            print(f"[{marker}] {result.name}: {result.observed}")
            if not result.passed and result.root_cause_area:
                print(f"       likely area: {result.root_cause_area}")
        summary = payload["summary"]
        print(f"\nSummary: {summary['passed']}/{summary['total']} passed")
    return 0 if all(result.passed for result in results) else 1


if __name__ == "__main__":
    sys.exit(main())
