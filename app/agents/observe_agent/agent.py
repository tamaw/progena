"""Observe agent (A2A).

Reads Progenitor's mounted state + logs (read-only) and returns a structured health/error report.
"""

import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _safe_read_text(path: Path, max_bytes: int = 512 * 1024) -> str:
    """Read up to max_bytes from end of file (best-effort) without holding locks."""
    try:
        if not path.exists() or not path.is_file():
            return ""
        size = path.stat().st_size
        if size <= max_bytes:
            return path.read_text(encoding="utf-8", errors="replace")
        with path.open("rb") as f:
            f.seek(max(0, size - max_bytes))
            data = f.read(max_bytes)
        return data.decode("utf-8", errors="replace")
    except Exception:
        return ""


def _safe_read_json(path: Path) -> Any:
    try:
        if not path.exists() or not path.is_file():
            return None
        content = path.read_text(encoding="utf-8", errors="replace")
        return json.loads(content)
    except Exception:
        return None


def _read_jsonl_tail(path: Path, limit: int = 50) -> list[dict[str, Any]]:
    text = _safe_read_text(path, max_bytes=512 * 1024)
    if not text:
        return []
    entries: list[dict[str, Any]] = []
    for line in text.splitlines()[-max(limit * 3, 200) :]:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                entries.append(obj)
        except Exception:
            continue
    return entries[-limit:]


def _extract_errorish_events(entries: list[dict[str, Any]], limit: int = 10) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for e in reversed(entries):
        if not isinstance(e, dict):
            continue
        event = str(e.get("event", "")).lower()
        error = e.get("error")
        level = str(e.get("level", "")).lower()
        if error or "error" in event or "fail" in event or level in ("error", "critical"):
            out.append(e)
        if len(out) >= limit:
            break
    return list(reversed(out))


def _truncate(text: Any, limit: int = 240) -> str:
    s = "" if text is None else str(text)
    s = " ".join(s.split())
    if len(s) <= limit:
        return s
    return s[: limit - 3] + "..."


def _event_ts(event: dict[str, Any]) -> str:
    for key in ("timestamp", "created_at", "time", "ts", "at"):
        v = event.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


class ObserveAgent:
    AGENT_ID = "observe_agent"
    AGENT_NAME = "Observe Agent"
    DESCRIPTION = "Monitors the Progenitor runtime by reading mounted state + logs and summarizing health/errors."
    CAPABILITIES = [
        "progenitor_health_report",
        "diagnose_last_error",
        "tail_state_and_logs",
    ]

    def __init__(
        self,
        state_dir: Path | None = None,
        logs_dir: Path | None = None,
    ):
        self.state_dir = state_dir or Path(os.getenv("OBSERVE_STATE_DIR", "/app/data/state"))
        self.logs_dir = logs_dir or Path(os.getenv("OBSERVE_LOGS_DIR", "/app/logs"))

    def _read_state_bundle(self) -> dict[str, Any]:
        health = _safe_read_json(self.state_dir / "health.json") or {}
        legacy_state = _safe_read_json(self.state_dir / "state.json")
        current_state = _safe_read_json(self.state_dir / "current_state.json") or {}
        build_state = _safe_read_json(self.state_dir / "build_state.json") or {}
        schedule = _safe_read_json(self.state_dir / "schedule.json")
        registry = _safe_read_json(self.state_dir / "registry.json") or {}

        return {
            "paths": {
                "state_dir": str(self.state_dir),
                "logs_dir": str(self.logs_dir),
            },
            "health": health,
            "legacy_state": legacy_state,
            "current_state": {
                "run_id": current_state.get("run_id"),
                "cycle_count": current_state.get("cycle_count"),
                "actions_this_cycle": current_state.get("actions_this_cycle"),
                "messages_count": len(current_state.get("messages", []) or []),
                "backlog_count": len(current_state.get("backlog", []) or []),
            },
            "build_state": {
                "build_in_progress": bool(build_state.get("build_in_progress")),
                "build_phase": build_state.get("build_phase"),
                "build_started_at": build_state.get("build_started_at"),
                "backlog_count": len(build_state.get("backlog", []) or []),
                "current_work_item": build_state.get("current_work_item"),
            },
            "schedule": schedule,
            "registry": {
                "agents_count": len(registry.get("agents", []) or []) if isinstance(registry, dict) else None,
                "tools_count": len(registry.get("tools", []) or []) if isinstance(registry, dict) else None,
            },
        }

    def _read_log_bundle(self) -> dict[str, Any]:
        decisions = _read_jsonl_tail(self.logs_dir / "decisions.jsonl", limit=80)
        conversation = _read_jsonl_tail(self.logs_dir / "conversation.jsonl", limit=40)
        schedule = _read_jsonl_tail(self.logs_dir / "schedule.jsonl", limit=80)
        validation = _read_jsonl_tail(self.logs_dir / "validation.jsonl", limit=80)
        errors = _read_jsonl_tail(self.logs_dir / "errors.jsonl", limit=80)
        stderr_tail = _safe_read_text(self.logs_dir / "progenitor.client.stderr.log", max_bytes=256 * 1024).splitlines()[
            -80:
        ]

        errorish = []
        errorish.extend(_extract_errorish_events(decisions, limit=10))
        errorish.extend(_extract_errorish_events(schedule, limit=10))
        errorish.extend(_extract_errorish_events(validation, limit=10))
        errorish.extend(_extract_errorish_events(errors, limit=10))

        # Keep only the last N overall, preserving order.
        errorish = errorish[-15:]

        return {
            "recent": {"decisions": decisions[-15:], "schedule": schedule[-15:], "validation": validation[-15:], "errors": errors[-15:]},
            "recent_errorish_events": errorish,
            "stderr_tail": stderr_tail,
            "conversation_tail": conversation[-10:],
        }

    def _format_report(
        self,
        *,
        request: str,
        focus: str,
        state_bundle: dict[str, Any],
        log_bundle: dict[str, Any],
    ) -> str:
        lines: list[str] = []

        health = state_bundle.get("health") or {}
        current = state_bundle.get("current_state") or {}
        build = state_bundle.get("build_state") or {}
        registry = state_bundle.get("registry") or {}

        lines.append("Observe Agent Report")
        lines.append(f"time: {_utc_now_iso()}")
        if request:
            lines.append(f"request: {_truncate(request, 400)}")
        lines.append(f"focus: {focus}")
        lines.append("")

        lines.append("Health")
        lines.append(f"- status: {health.get('status', 'unknown')}")
        if health.get("last_active"):
            lines.append(f"- last_active: {health.get('last_active')}")
        if health.get("run_id"):
            lines.append(f"- run_id: {health.get('run_id')}")
        if health.get("cycle_count") is not None:
            lines.append(f"- cycle_count: {health.get('cycle_count')}")
        if health.get("backlog_count") is not None:
            lines.append(f"- backlog_count: {health.get('backlog_count')}")
        if health.get("schedule_count") is not None:
            lines.append(f"- schedule_count: {health.get('schedule_count')}")
        if health.get("error"):
            lines.append(f"- error: {_truncate(health.get('error'), 600)}")
        lines.append("")

        lines.append("State Summary")
        lines.append(f"- messages_count: {current.get('messages_count', 0)}")
        lines.append(f"- current.backlog_count: {current.get('backlog_count', 0)}")
        lines.append(f"- build_in_progress: {build.get('build_in_progress', False)}")
        if build.get("build_phase"):
            lines.append(f"- build_phase: {build.get('build_phase')}")
        if build.get("build_started_at"):
            lines.append(f"- build_started_at: {build.get('build_started_at')}")
        lines.append(f"- build.backlog_count: {build.get('backlog_count', 0)}")
        if registry.get("agents_count") is not None:
            lines.append(f"- registry.agents_count: {registry.get('agents_count')}")
        if registry.get("tools_count") is not None:
            lines.append(f"- registry.tools_count: {registry.get('tools_count')}")
        lines.append("")

        errorish = log_bundle.get("recent_errorish_events") or []
        stderr_tail = log_bundle.get("stderr_tail") or []

        if focus in ("errors", "logs"):
            lines.append("Recent Error Signals")
            if not errorish:
                lines.append("- (none found in decisions/schedule/validation/errors logs)")
            else:
                for idx, e in enumerate(errorish[-10:], start=1):
                    if not isinstance(e, dict):
                        continue
                    ts = _event_ts(e)
                    event = e.get("event") or e.get("type") or ""
                    msg = e.get("error") or e.get("message") or e.get("detail") or ""
                    prefix = f"{idx}) "
                    ts_part = f"[{ts}] " if ts else ""
                    lines.append(f"{prefix}{ts_part}{_truncate(event, 80)} - {_truncate(msg, 400)}")
            lines.append("")

            if stderr_tail:
                lines.append("progenitor.client.stderr.log (tail)")
                for line in stderr_tail[-30:]:
                    lines.append(f"> {line}")
                lines.append("")

        return "\n".join(lines).rstrip() + "\n"

    async def process(self, request: str, context: dict[str, Any]) -> dict[str, Any]:
        req = (request or "").strip()
        state_bundle = self._read_state_bundle()
        log_bundle = self._read_log_bundle()

        # If user asked "why error", bias toward error-focused output.
        req_lower = req.lower()
        focus = "status"
        if any(k in req_lower for k in ("why", "error", "failed", "crash", "exception", "broken")):
            focus = "errors"
        elif any(k in req_lower for k in ("log", "logs", "tail")):
            focus = "logs"

        report = self._format_report(
            request=req,
            focus=focus,
            state_bundle=state_bundle,
            log_bundle=log_bundle,
        )

        health = state_bundle.get("health") or {}
        metadata = {
            "focus": focus,
            "paths": state_bundle.get("paths"),
            "health_status": health.get("status", "unknown"),
            "health_last_active": health.get("last_active"),
            "health_run_id": health.get("run_id"),
            "health_cycle_count": health.get("cycle_count"),
            "recent_errorish_count": len(log_bundle.get("recent_errorish_events") or []),
            "context_keys": sorted(context.keys()) if isinstance(context, dict) else [],
        }

        return {"report": report, "metadata": metadata}


app = FastAPI()
agent = ObserveAgent()
_start_time = time.time()


@app.on_event("startup")
async def startup_event() -> None:
    port = int(os.environ.get("PORT", "8080"))
    print("STATUS:starting:Initializing Observe Agent", flush=True)
    print(f"STATUS:running:Server started on port {port}", flush=True)
    print("STATUS:waiting:Idle, waiting for requests", flush=True)


@app.on_event("shutdown")
async def shutdown_event() -> None:
    print("STATUS:stopping:Shutting down", flush=True)
    print("STATUS:stopped:Agent terminated", flush=True)


@app.get("/.well-known/agent.json")
async def agent_card() -> JSONResponse:
    port = int(os.environ.get("PORT", "8080"))
    payload = {
        "name": agent.AGENT_NAME,
        "description": agent.DESCRIPTION,
        "version": "0.1.0",
        "protocol": "a2a",
        "capabilities": agent.CAPABILITIES,
        "endpoint": f"http://localhost:{port}",
    }
    return JSONResponse(content=payload)


@app.get("/health")
async def health() -> JSONResponse:
    uptime = time.time() - _start_time
    payload = {
        "status": "healthy",
        "name": agent.AGENT_NAME,
        "version": "0.1.0",
        "uptime_seconds": float(uptime),
        "timestamp": _utc_now_iso(),
    }
    return JSONResponse(content=payload)


@app.post("/")
async def handle(req: Request) -> JSONResponse:
    try:
        body = await req.json()
    except Exception:
        body = {}
    req_str = body.get("request", "") if isinstance(body, dict) else ""
    ctx = body.get("context", {}) if isinstance(body, dict) else {}

    print("STATUS:executing:Reading state/logs and generating report", flush=True)
    try:
        result = await agent.process(req_str, ctx)
        report = result.get("report", "")
        meta = result.get("metadata", {}) if isinstance(result, dict) else {}
        response = {
            "success": True,
            "response": report,
            "error": None,
            "metadata": meta,
        }
        print("STATUS:waiting:Idle, waiting for requests", flush=True)
        return JSONResponse(content=response)
    except Exception as e:
        print(f"STATUS:failed:{e}", flush=True)
        err: dict[str, Any] = {
            "success": False,
            "response": None,
            "error": "Observe agent failed to generate report",
            "metadata": {"detail": str(e)},
        }
        return JSONResponse(content=err, status_code=500)

