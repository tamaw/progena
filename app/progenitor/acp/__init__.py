"""Agent Client Protocol (ACP) client subsystem for the Progenitor runtime.

This package is the ACP peer of ``app/progenitor/a2a/``. Where the A2A subsystem
lets Progenitor invoke remote A2A agents over HTTP, this subsystem lets Progenitor
*drive* local ACP agents (the Agent Client Protocol — Zed's stdio JSON-RPC
standard, as implemented by ``copilot --acp``) as the ACP **client**.

It is built on the installed ``agent-client-protocol`` SDK (imported as ``acp``):
``acp.spawn_agent_process`` + ``acp.Client`` + ``ClientSideConnection``. The
machinery is agent-agnostic; GitHub Copilot is just one configured command.

Note this is the *inverse* of LangChain's ``deepagents-acp``, which exposes a deep
agent *as* an ACP server. There is no client-side helper in LangGraph/DeepAgents,
so the client runner here is built directly on the ACP SDK.
"""

from __future__ import annotations

from .agents import ACP_PROFILES, build_agent_args, build_copilot_args, parse_acp_connection
from .client import ACPSessionClient
from .runner import ACPRunner, ACPRunResult
from .tasks import (
    build_acp_completion_message,
    build_acp_task_response,
    dispatch_tracked_acp_task,
    send_acp_completion_notification,
)

__all__ = [
    "ACP_PROFILES",
    "ACPRunner",
    "ACPRunResult",
    "ACPSessionClient",
    "build_acp_completion_message",
    "build_acp_task_response",
    "build_agent_args",
    "build_copilot_args",
    "dispatch_tracked_acp_task",
    "parse_acp_connection",
    "send_acp_completion_notification",
]
