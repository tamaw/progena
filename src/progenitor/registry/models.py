"""Registry domain contracts."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from src.progenitor.acp.models import ACPAgentConfig
from src.progenitor.utils import utcnow


class AgentStatus(StrEnum):
    """Runtime status of an A2A agent."""

    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    EXECUTING = "executing"
    WAITING = "waiting"
    FAILED = "failed"
    STOPPING = "stopping"


class AgentRegistryEntry(BaseModel):
    """An agent or MCP server in the registry."""

    name: str
    kind: str = "a2a_agent"
    version: str = "0.1.0"
    description: str = ""
    capabilities: list[str] = Field(default_factory=list)
    entrypoint: str = ""

    transport: str = "http"
    port: int | None = None
    url: str | None = None
    headers: dict[str, str] | None = None
    endpoint: str | None = None
    agent_card_url: str | None = None

    # Set for ACP agents (transport == "acp"); None for A2A/MCP entries.
    acp: ACPAgentConfig | None = None

    status: AgentStatus = AgentStatus.STOPPED
    status_message: str | None = None
    pid: int | None = None
    startup_time: datetime | None = None
    last_heartbeat: datetime | None = None
    last_error: str | None = None

    enabled: bool = True
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
