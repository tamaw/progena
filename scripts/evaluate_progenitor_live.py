#!/usr/bin/env python3
"""Run repeatable live evaluations against a running Progenitor instance."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

import httpx
from _live_eval_http import (
    DEFAULT_BASE_URL,
    EventCollector,
    http_assistant_response,
    http_stream_items,
)

ASYNC_SUBAGENT_RESULT = "HELLO_FROM_ASYNC_SUBAGENT"


@dataclass
class EvaluationResult:
    name: str
    passed: bool
    expectation: str
    observed: str
    details: dict[str, Any] = field(default_factory=dict)
    root_cause_area: str | None = None


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


async def eval_runtime_health(client: httpx.AsyncClient, _timeout: float) -> EvaluationResult:
    name = "runtime_health_check"
    expectation = "The runtime HTTP server responds 200 OK on GET /health."
    try:
        response = await client.get("/health")
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        return _fail(
            name, expectation, f"health check failed: {exc}", root_cause_area="runtime/http.py"
        )
    return _pass(name, expectation, f"health responded: {payload}")


async def eval_chat_non_streaming(client: httpx.AsyncClient, timeout: float) -> EvaluationResult:
    marker = f"LIVE_EVAL_OK_{uuid.uuid4().hex[:8]}"
    name = "chat_llm_turn"
    expectation = f"A chat request returns a response containing {marker}."
    prompt = f"Reply with exactly this token and no other text: {marker}"
    try:
        content = await http_assistant_response(
            client,
            timeout,
            prompt,
            metadata={"source": "live_evaluation", "mode": "non_streaming"},
        )
        if marker in content:
            return _pass(name, expectation, "response contained expected marker")
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
            f"chat turn failed: {exc}",
            root_cause_area="runtime/http_chat.py or agent/adapters.py",
        )


async def eval_chat_streaming(client: httpx.AsyncClient, timeout: float) -> EvaluationResult:
    marker = f"LIVE_EVAL_STREAM_OK_{uuid.uuid4().hex[:8]}"
    name = "chat_streaming_llm_turn"
    expectation = f"Streaming chat returns stream_chunk items with content containing {marker}."
    prompt = f"Reply with exactly this token and no other text: {marker}"
    chunks: list[str] = []
    event_types: list[str] = []
    try:
        items = await http_stream_items(
            client, timeout, prompt, metadata={"source": "live_evaluation", "mode": "streaming"}
        )
        for item in items:
            item_type = item.get("type")
            if item_type == "stream_event":
                event_types.append(str(item.get("event_type") or "unknown"))
            elif item_type == "stream_chunk":
                chunks.append(str(item.get("content") or ""))
    except Exception as exc:
        return _fail(
            name,
            expectation,
            f"streaming chat turn failed: {exc}",
            root_cause_area="runtime/http_chat.py or sse/deepagent_stream.py",
        )

    combined = "".join(chunks)
    if marker in combined:
        return _pass(
            name,
            expectation,
            "stream chunks contained marker",
            chunk_count=len(chunks),
            stream_events=event_types,
        )
    return _fail(
        name,
        expectation,
        f"stream did not contain marker: content={combined[:300]!r}",
        root_cause_area="sse/deepagent_stream.py or live LLM configuration",
        chunk_count=len(chunks),
        stream_events=event_types,
    )


async def eval_reasoning_stream(client: httpx.AsyncClient, timeout: float) -> EvaluationResult:
    name = "reasoning_stream"
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
                items = await http_stream_items(
                    client,
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
        root_cause_area="sse/deepagent_stream.py or live model/provider reasoning behavior",
        attempts=attempts,
    )


async def eval_tool_catalog(client: httpx.AsyncClient, timeout: float) -> EvaluationResult:
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
        response = await http_assistant_response(
            client,
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


async def eval_microsoft_learn_mcp(client: httpx.AsyncClient, timeout: float) -> EvaluationResult:
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
        check_response = await http_assistant_response(
            client,
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
        items = await http_stream_items(
            client,
            max(timeout, 120.0),
            tool_prompt,
            metadata={"source": "live_evaluation", "mode": "microsoft_learn_mcp"},
        )
    except Exception as exc:
        return _fail(
            name,
            expectation,
            f"Microsoft Learn MCP tool call failed: {exc}",
            root_cause_area="MCP runtime wiring, sse/deepagent_stream.py, or Microsoft Learn MCP availability",
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
        root_cause_area="MCP runtime wiring, sse/deepagent_stream.py, or Microsoft Learn MCP behavior",
        catalog_response=check_response,
        tool_events=tool_events,
        response=combined,
    )


async def eval_subagent_delegation(client: httpx.AsyncClient, timeout: float) -> EvaluationResult:
    name = "subagent_delegation_via_chat"
    expectation = (
        "A streaming chat request can delegate to the repo-manager subagent, emit subagent_start and "
        "subagent_result events, and return a non-empty answer to the user."
    )
    prompt = (
        "Use the repo-manager subagent for this request. "
        "Ask it to run git rev-parse --show-toplevel and report the result concisely."
    )
    try:
        items = await http_stream_items(
            client,
            timeout,
            prompt,
            metadata={"source": "live_evaluation", "mode": "subagent_delegation"},
        )
    except Exception as exc:
        return _fail(
            name,
            expectation,
            f"subagent delegation request failed: {exc}",
            root_cause_area="agent/agent.py, agent/object_agent_registry.py, or sse/deepagent_stream.py",
        )

    event_types = [
        str(item.get("event_type") or "") for item in items if item.get("type") == "stream_event"
    ]
    final_text = "".join(
        str(item.get("content") or "") for item in items if item.get("type") == "stream_chunk"
    )
    if "subagent_start" in event_types and "subagent_result" in event_types and final_text.strip():
        return _pass(
            name,
            expectation,
            "repo-manager subagent delegated and returned a non-empty answer",
            stream_events=event_types,
            final_text=final_text,
        )
    return _fail(
        name,
        expectation,
        "subagent delegation did not surface the expected events and final answer",
        root_cause_area="agent/object_agent_registry.py, agent/agent.py, or sse/stream_events.py",
        stream_events=event_types,
        final_text=final_text[:500],
    )


async def eval_async_subagent_background_completion(
    client: httpx.AsyncClient,
    timeout: float,
) -> EvaluationResult:
    name = "async_subagent_background_completion"
    expectation = (
        "A chat request can launch hello-world-async via start_async_task, surface async kickoff "
        "telemetry, return a task id immediately, and later receive a heartbeat-driven event "
        f"callback with {ASYNC_SUBAGENT_RESULT}."
    )
    prompt = (
        "Use the start_async_task tool with subagent_type hello-world-async. "
        f"description should say to return exactly {ASYNC_SUBAGENT_RESULT}. "
        "Do not call check_async_task, list_async_tasks, update_async_task, or cancel_async_task. "
        "Reply with exactly TASK_ID=<task_id>."
    )
    try:
        async with EventCollector(client) as collector:
            kickoff_items = await http_stream_items(
                client,
                max(timeout, 120.0),
                prompt,
                metadata={"source": "live_evaluation", "mode": "async_subagent"},
            )

            stream_events = [
                str(item.get("event_type") or "")
                for item in kickoff_items
                if item.get("type") == "stream_event"
            ]
            final_text = "".join(
                str(item.get("content") or "")
                for item in kickoff_items
                if item.get("type") == "stream_chunk"
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

            def _is_completion(event: dict[str, Any]) -> bool:
                if str(event.get("event_type") or "") != "agent_message":
                    return False
                content = str(event.get("content") or "")
                metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
                serialized = json.dumps(metadata, default=str)
                return (
                    ASYNC_SUBAGENT_RESULT in content
                    and task_id in serialized
                    and metadata.get("source") == "async_subagent_completion"
                )

            callback = await collector.wait_for(_is_completion, deadline=time.time() + 45.0)
    except Exception as exc:
        return _fail(
            name,
            expectation,
            f"async subagent live eval failed: {exc}",
            root_cause_area=(
                "agent/async_subagents.py, agent/middleware.py, async_subagents/, or heartbeat async-subagent nodes"
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
            "async subagent launched, returned a task id, and later emitted a completion event",
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
            "agent/middleware.py, async_subagents/, runtime/http_chat.py, or heartbeat async-subagent nodes"
        ),
        task_id=task_id,
        stream_events=stream_events,
        final_text=final_text,
        callback=callback,
    )


async def eval_self_reflect_tool(client: httpx.AsyncClient, timeout: float) -> EvaluationResult:
    name = "self_reflect_via_chat"
    expectation = "The live agent can use self_reflect to inspect its codebase."
    prompt = (
        "Use the self_reflect tool with file_path config.py to read that file. If that succeeds, reply with exactly "
        "FOUND_CONFIG."
    )
    try:
        response = await http_assistant_response(
            client,
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


async def eval_conversation_history(
    client: httpx.AsyncClient, timeout: float
) -> tuple[EvaluationResult, str]:
    token = "marzipan"
    name = "conversation_history"
    expectation = (
        "The current thread remembers the previous user message and can recall a token from it."
    )
    try:
        first = await http_assistant_response(
            client,
            timeout,
            f"For this conversation only, the code word is {token}.",
            metadata={"source": "live_evaluation", "mode": "conversation_history_start"},
        )
        second = await http_assistant_response(
            client,
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


async def eval_new_conversation_reset(
    client: httpx.AsyncClient,
    timeout: float,
    prior_token: str,
) -> EvaluationResult:
    name = "new_conversation_reset"
    expectation = (
        "new_conversation starts a fresh conversation so the old token is no longer available."
    )
    try:
        await http_assistant_response(
            client,
            timeout,
            "new conversation",
            metadata={"source": "live_evaluation", "mode": "new_conversation"},
        )
        recall = await http_assistant_response(
            client,
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


async def eval_schedule_create_and_trigger(
    client: httpx.AsyncClient,
    timeout: float,
) -> EvaluationResult:
    name = "schedule_create_and_trigger"
    token = f"SCHEDULE_TOKEN_{uuid.uuid4().hex[:8]}"
    expectation = (
        "A one-off scheduled task can be created via chat, confirmed via list_schedule, "
        "and later trigger scheduled_response/work_completed events."
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
    observed_events: list[str] = []
    response_text = ""
    try:
        async with EventCollector(client) as collector:
            response_text = await http_assistant_response(
                client,
                max(timeout, 120.0),
                prompt,
                metadata={"source": "live_evaluation", "mode": "schedule"},
            )
            schedule_listing = ""
            for _ in range(12):
                schedule_listing = await http_assistant_response(
                    client,
                    timeout,
                    "Use the list_schedule tool and reply with the raw schedule list only.",
                    metadata={"source": "live_evaluation", "mode": "schedule_check"},
                )
                if token in schedule_listing:
                    break
                await asyncio.sleep(1.0)
            else:
                raise RuntimeError(
                    f"list_schedule did not expose the scheduled token: {schedule_listing!r}"
                )

            def _matches(event: dict[str, Any]) -> bool:
                content = str(event.get("content") or "")
                metadata = event.get("metadata") or {}
                if token in content or token in json.dumps(metadata, default=str):
                    event_type = str(event.get("event_type") or "")
                    if event_type not in observed_events:
                        observed_events.append(event_type)
                return {"scheduled_response", "work_completed"} <= set(observed_events)

            await collector.wait_for(_matches, deadline=time.time() + 100.0)
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


async def eval_memory_backend_write(client: httpx.AsyncClient, timeout: float) -> EvaluationResult:
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
        response = await http_assistant_response(
            client,
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


async def run(args: argparse.Namespace) -> list[EvaluationResult]:
    async with httpx.AsyncClient(
        base_url=args.base_url, timeout=httpx.Timeout(args.runtime_timeout, read=None)
    ) as client:
        results = [await eval_runtime_health(client, args.runtime_timeout)]
        results.append(await eval_tool_catalog(client, args.llm_timeout))
        results.append(await eval_microsoft_learn_mcp(client, args.llm_timeout))
        results.append(await eval_subagent_delegation(client, args.llm_timeout))
        results.append(await eval_async_subagent_background_completion(client, args.llm_timeout))
        history_result, history_token = await eval_conversation_history(client, args.llm_timeout)
        results.append(history_result)
        results.append(await eval_new_conversation_reset(client, args.llm_timeout, history_token))
        results.append(await eval_reasoning_stream(client, args.llm_timeout))
        if not args.skip_llm:
            results.append(await eval_chat_non_streaming(client, args.llm_timeout))
            results.append(await eval_chat_streaming(client, args.llm_timeout))
            if not args.skip_schedule:
                results.append(await eval_schedule_create_and_trigger(client, args.llm_timeout))
            if not args.skip_memory_write:
                results.append(await eval_memory_backend_write(client, args.llm_timeout))
        return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--runtime-timeout", type=float, default=10.0)
    parser.add_argument("--llm-timeout", type=float, default=240.0)
    parser.add_argument("--skip-llm", action="store_true")
    parser.add_argument("--skip-schedule", action="store_true")
    parser.add_argument("--skip-memory-write", action="store_true")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON results.")
    args = parser.parse_args()

    results = asyncio.run(run(args))
    payload = {
        "target": {
            "base_url": args.base_url,
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
