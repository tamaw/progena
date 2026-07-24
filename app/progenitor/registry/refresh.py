"""Agent registry discovery refresh helpers."""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


async def _tcp_reachable(host: str, port: int, timeout: float) -> bool:
    """Return True if a TCP connection to ``host:port`` opens within ``timeout``."""
    try:
        fut = asyncio.open_connection(host, port)
        _reader, writer = await asyncio.wait_for(fut, timeout=max(0.1, timeout))
    except OSError:  # connection refused/unreachable, or TimeoutError (an OSError subclass)
        return False
    writer.close()
    with contextlib.suppress(Exception):
        await writer.wait_closed()
    return True


async def discover_local_agents(
    *,
    project_root: Path,
    llm_client: Any,
    registry: Any,
    state_manager: Any = None,
) -> dict[str, Any]:
    """Run local agent discovery against app/agents."""
    try:
        from .discovery import discover_agents

        agents_dir = project_root / "app" / "agents"
        if not agents_dir.exists():
            return {"agents_found": 0, "agents_registered": 0}
        discovery = await discover_agents(
            agents_dir=agents_dir,
            llm_client=llm_client,
            registry=registry,
            state_manager=state_manager,
        )
        logger.info(
            "Startup discovery: found=%d registered=%d",
            discovery.get("agents_found", 0),
            discovery.get("agents_registered", 0),
        )
        return dict(discovery)
    except Exception as exc:
        logger.debug("Startup local agent discovery skipped: %s", exc)
        return {"error": str(exc)}


async def refresh_agent_cards(
    registry: Any,
    a2a_client: Any,
    *,
    timeout: float = 2.0,
    concurrency: int = 8,
    persist: bool = False,
) -> dict[str, Any]:
    """Best-effort refresh agent cards in memory."""
    agents = registry.get("agents", []) if isinstance(registry, dict) else registry.get_agents()
    if not agents:
        return {"agents_checked": 0, "agents_updated": 0, "errors": []}

    sem = asyncio.Semaphore(max(1, int(concurrency)))
    updated = 0
    errors: list[str] = []
    changed_any = False

    async def _refresh_one(agent: dict[str, Any]) -> None:
        nonlocal updated, changed_any
        name = str(agent.get("name") or "")
        if not name:
            return
        if not agent.get("url") and not agent.get("agent_card_url"):
            return

        async with sem:
            try:
                card = await a2a_client.get_agent_card(agent, timeout=timeout)
            except Exception as exc:
                errors.append(f"{name}: {exc}")
                return

        if not isinstance(card, dict):
            return

        agent["agent_card"] = card
        updated += 1
        changed_any = True

    await asyncio.gather(*[_refresh_one(agent) for agent in agents if isinstance(agent, dict)])

    save = getattr(registry, "save", None)
    if changed_any and persist and callable(save):
        result = save()
        if inspect.isawaitable(result):
            await result

    return {"agents_checked": len(agents), "agents_updated": updated, "errors": errors}


async def refresh_agent_health(
    registry: Any,
    *,
    timeout: float = 2.0,
    concurrency: int = 8,
    persist: bool = False,
) -> dict[str, Any]:
    """Best-effort refresh of /health status for configured agents."""
    import aiohttp

    from ..state.models import AgentStatus

    agents = registry.get("agents", []) if isinstance(registry, dict) else registry.get_agents()
    if not agents:
        return {"agents_checked": 0, "healthy": 0, "unhealthy": 0, "errors": []}

    sem = asyncio.Semaphore(max(1, int(concurrency)))
    healthy = 0
    unhealthy = 0
    errors: list[str] = []
    changed_any = False

    async def _check_one(agent: dict[str, Any]) -> None:
        nonlocal healthy, unhealthy, changed_any
        name = str(agent.get("name") or "")
        if not name:
            return
        # ACP agents are not HTTP servers. TCP-mode agents (host/port) connect to an
        # already-running server, so probe reachability; stdio agents are local CLIs,
        # so derive status from whether the command resolves on PATH.
        if str(agent.get("transport") or "").lower() == "acp":
            acp_cfg = agent.get("acp") or {}
            port = acp_cfg.get("port")
            if port is not None:
                host = str(acp_cfg.get("host") or "127.0.0.1").strip() or "127.0.0.1"
                endpoint = f"{host}:{port}"
                reachable = await _tcp_reachable(host, int(port), timeout)
                new_status = AgentStatus.RUNNING.value if reachable else AgentStatus.FAILED.value
                status_message = (
                    f"ACP server {endpoint} reachable"
                    if reachable
                    else f"ACP server {endpoint} not reachable"
                )
            else:
                import shutil

                command = str(acp_cfg.get("command") or "").strip()
                reachable = bool(command) and shutil.which(command) is not None
                new_status = AgentStatus.RUNNING.value if reachable else AgentStatus.FAILED.value
                status_message = (
                    f"ACP command {command!r} available"
                    if reachable
                    else f"ACP command {command!r} not found on PATH"
                )
            if agent.get("status") != new_status:
                changed_any = True
            agent["status"] = new_status
            agent["status_message"] = status_message
            if reachable:
                healthy += 1
            else:
                unhealthy += 1
            return
        url = str(agent.get("url") or "").strip().rstrip("/")
        if not url:
            return
        health_url = f"{url}/health"

        async with sem:
            try:
                async with (
                    aiohttp.ClientSession() as session,
                    session.get(
                        health_url,
                        timeout=aiohttp.ClientTimeout(total=timeout),
                    ) as response,
                ):
                    if response.status < 400:
                        healthy += 1
                        if agent.get("status") != AgentStatus.RUNNING.value:
                            changed_any = True
                        agent["status"] = AgentStatus.RUNNING.value
                        agent["status_message"] = "Health check passed"
                        return
                    raise RuntimeError(f"HTTP {response.status}")
            except Exception as exc:
                unhealthy += 1
                errors.append(f"{name}: {exc}")
                if agent.get("status") != AgentStatus.FAILED.value:
                    changed_any = True
                agent["status"] = AgentStatus.FAILED.value
                agent["status_message"] = f"Health check failed: {exc}"

    await asyncio.gather(*[_check_one(agent) for agent in agents if isinstance(agent, dict)])

    save = getattr(registry, "save", None)
    if changed_any and persist and callable(save):
        result = save()
        if inspect.isawaitable(result):
            await result

    return {
        "agents_checked": len(agents),
        "healthy": healthy,
        "unhealthy": unhealthy,
        "errors": errors,
    }


async def refresh_agent_metadata(registry: Any) -> dict[str, Any]:
    """Refresh agent cards and health with best-effort logging."""
    try:
        from ..a2a import A2AClient

        a2a_client = A2AClient()
        card_summary = await refresh_agent_cards(registry, a2a_client)
        logger.info(
            "Agent card refresh: checked=%d updated=%d errors=%d",
            card_summary.get("agents_checked", 0),
            card_summary.get("agents_updated", 0),
            len(card_summary.get("errors") or []),
        )
        health_summary = await refresh_agent_health(registry)
        logger.info(
            "Agent health refresh: checked=%d healthy=%d unhealthy=%d",
            health_summary.get("agents_checked", 0),
            health_summary.get("healthy", 0),
            health_summary.get("unhealthy", 0),
        )
        return {
            "agent_card_refresh": card_summary,
            "agent_health_refresh": health_summary,
        }
    except Exception as exc:
        logger.debug("Agent card refresh skipped: %s", exc)
        return {"error": str(exc)}
