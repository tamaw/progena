"""
Async subagent descriptors for the Progenitor DeepAgent.

Wraps the deterministic background-delegation smoke test as a DeepAgent
async subagent. Regular (synchronous) subagents are no longer code-defined
here -- they are wired in exclusively as manifest-backed object agents (see
agent/object_agents.py) or routine graphs (see routines/subagent.py).
"""

from __future__ import annotations

from deepagents.middleware.async_subagents import AsyncSubAgent

from src.progenitor.async_subagents.tasks import HELLO_WORLD_ASYNC_ASSISTANT_ID


def create_hello_world_async_subagent(http_base_url: str) -> AsyncSubAgent:
    """Create the deterministic async subagent used for background delegation tests."""
    return AsyncSubAgent(
        name="hello-world-async",
        description=(
            "Runs in the background and later returns the fixed token "
            "HELLO_FROM_ASYNC_SUBAGENT. Use when explicitly asked to test async "
            "subagent delegation or background completion callbacks."
        ),
        graph_id=HELLO_WORLD_ASYNC_ASSISTANT_ID,
        url=f"{http_base_url.rstrip('/')}/agent-protocol",
    )


def create_progenitor_async_subagents(http_base_url: str) -> list[AsyncSubAgent]:
    """Create Progenitor's background async subagents."""
    return [create_hello_world_async_subagent(http_base_url)]
