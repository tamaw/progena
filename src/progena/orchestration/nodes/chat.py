"""DeepAgent chat node."""

from __future__ import annotations

import time
from typing import Any

from langchain_core.messages import HumanMessage
from langgraph.runtime import Runtime

from src.progena.llm.provider_context import ProviderTurn, activate_provider_turn
from src.progena.orchestration.nodes.context import HeartbeatContext
from src.progena.orchestration.state import ProgenaState
from src.progena.runtime.instance import RUNTIME_INSTANCE_ID


async def chat_node(
    state: ProgenaState,
    runtime: Runtime[HeartbeatContext],
) -> dict[str, Any]:
    current_input = str(state.get("current_input") or "").strip()
    if not current_input:
        return {}

    thread_id = str(state.get("thread_id") or "progena_main")
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
    with activate_provider_turn(turn):
        result = await runtime.context.agent_graph.ainvoke(
            {"messages": [human_message]},
            config={
                "configurable": {"thread_id": thread_id},
                "metadata": {"runtime_instance": RUNTIME_INSTANCE_ID},
            },
        )
    return result if isinstance(result, dict) else {"messages": []}
