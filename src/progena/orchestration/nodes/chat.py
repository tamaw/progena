"""DeepAgent chat node."""

from __future__ import annotations

import time
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage
from langgraph.runtime import Runtime

from src.progena.llm.provider_context import ProviderTurn, activate_provider_turn
from src.progena.orchestration.nodes.context import HeartbeatContext
from src.progena.orchestration.state import ProgenaState
from src.progena.runtime.conversation_gate import ConversationTurnGate
from src.progena.runtime.instance import RUNTIME_INSTANCE_ID
from src.progena.threads import MAIN_THREAD_ID


async def chat_node(
    state: ProgenaState,
    runtime: Runtime[HeartbeatContext],
) -> dict[str, Any]:
    current_input = str(state.get("current_input") or "").strip()
    if not current_input:
        return {}

    thread_id = str(state.get("thread_id") or MAIN_THREAD_ID)
    input_metadata = state.get("current_input_metadata")
    additional_kwargs: dict[str, Any] = {"created_at": time.time()}
    if isinstance(input_metadata, dict) and isinstance(input_metadata.get("attachments"), list):
        additional_kwargs["attachments"] = input_metadata["attachments"]
    # DeepAgent returns the updated conversation state for this turn. Snapshot
    # the selected DB provider before graph execution so a Settings save cannot
    # mix models or credentials into a running heartbeat turn.
    human_message = HumanMessage(content=current_input, additional_kwargs=additional_kwargs)
    llm_config = getattr(getattr(runtime.context, "llm_client", None), "config", None)
    turn = await ProviderTurn.create(llm_config, session_id=thread_id)

    async def invoke() -> Any:
        config = {
            "configurable": {"thread_id": thread_id},
            "metadata": {"runtime_instance": RUNTIME_INSTANCE_ID},
        }
        before_state = await runtime.context.agent_graph.aget_state(config)
        before_ids = {
            message.id
            for message in before_state.values.get("messages", [])
            if isinstance(message, BaseMessage) and isinstance(message.id, str)
        }
        try:
            with activate_provider_turn(turn):
                result = await runtime.context.agent_graph.ainvoke(
                    {"messages": [human_message]}, config=config
                )
        except Exception:
            current_state = await runtime.context.agent_graph.aget_state(config)
            await _settle_queue_claims(
                runtime, thread_id, current_state.values, before_ids, status="failed"
            )
            raise
        if isinstance(result, dict):
            await _settle_queue_claims(runtime, thread_id, result, before_ids, status="completed")
        return result

    gate = getattr(runtime.context, "conversation_gate", None)
    if not isinstance(gate, ConversationTurnGate):
        gate = ConversationTurnGate()
        runtime.context.conversation_gate = gate
    result = await gate.run(thread_id, invoke)
    return result if isinstance(result, dict) else {"messages": []}


async def _settle_queue_claims(
    runtime: Runtime[HeartbeatContext],
    thread_id: str,
    result: dict[str, Any],
    before_ids: set[str],
    *,
    status: str,
) -> None:
    """Settle only native queue claims created by this gated invocation."""
    queue = getattr(runtime.context.agent_graph, "chat_queue", None)
    if queue is None:
        return
    messages = result.get("messages")
    if not isinstance(messages, list):
        return
    claims = [
        (message.id, message.additional_kwargs.get("progena_queue_request_id"))
        for message in messages
        if isinstance(message, BaseMessage)
        and isinstance(message.id, str)
        and message.id not in before_ids
        and isinstance(message.additional_kwargs.get("progena_queue_request_id"), str)
    ]
    for message_id, request_id in claims:
        await queue.discard_claim(thread_id, message_id)
        await runtime.context.queues.push_event(
            f"chat_queue_execution_{status}",
            "",
            {
                "conversation_id": thread_id,
                "request_id": request_id,
                "content_request_id": request_id,
                "message_id": message_id,
                "response": {
                    "id": request_id,
                    "object": "response",
                    "status": status,
                    "output": [],
                },
            },
        )
