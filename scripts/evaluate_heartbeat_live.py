#!/usr/bin/env python3
"""Run repeatable live evaluations against the running Progenitor Heartbeat loop."""

from __future__ import annotations

import argparse
import json
import os
import socket
import struct
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib import error, request

DEFAULT_HTTP_BASE = "http://127.0.0.1:9501"
DEFAULT_IPC_HOST = "127.0.0.1"
DEFAULT_IPC_PORT = 9500
DEFAULT_STATE_DIR = Path("data/state")
DEFAULT_CONFIG_DIR = Path("data/config")
HEADER_SIZE = 4
ENCODING = "utf-8"


@dataclass
class EvaluationResult:
    name: str
    passed: bool
    expectation: str
    observed: str
    details: dict[str, Any] = field(default_factory=dict)
    root_cause_area: str | None = None


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


def _read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding=ENCODING))
    except json.JSONDecodeError:
        return default


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".live-eval-tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding=ENCODING)
    tmp_path.replace(path)


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
                "request_id": f"heartbeat-live-eval-{uuid.uuid4().hex[:8]}",
                "metadata": metadata or {"source": "heartbeat_live_evaluation"},
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


def _reserve_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        return int(sock.getsockname()[1])


def _wait_for_http_ready(url: str, timeout: float) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            status, _body = _http_json(url, timeout=5.0)
            if status == 200:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def _start_test_a2a_agent(timeout: float) -> tuple[subprocess.Popen[bytes], int]:
    port = _reserve_port()
    env = dict(os.environ)
    env["TEST_A2A_AGENT_PORT"] = str(port)
    env["TEST_A2A_AGENT_HOST"] = "127.0.0.1"
    pythonpath = env.get("PYTHONPATH", "").strip()
    env["PYTHONPATH"] = str(Path.cwd()) if not pythonpath else f"{Path.cwd()}:{pythonpath}"
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "scripts.test_a2a_eval_agent:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=Path.cwd(),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if not _wait_for_http_ready(f"http://127.0.0.1:{port}/health", timeout):
        process.terminate()
        raise RuntimeError("test A2A agent did not become ready")
    return process, port


def _stop_process(process: subprocess.Popen[bytes] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5.0)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5.0)


def _http_json(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    timeout: float = 10.0,
) -> tuple[int, dict[str, Any]]:
    body = json.dumps(payload).encode(ENCODING) if payload is not None else None
    headers = {"Accept": "application/json"}
    if payload is not None:
        headers["Content-Type"] = "application/json"
    req = request.Request(url, data=body, method=method, headers=headers)
    try:
        with request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode(ENCODING)
            return response.status, json.loads(raw or "{}")
    except error.HTTPError as exc:
        raw = exc.read().decode(ENCODING, errors="replace")
        try:
            parsed = json.loads(raw or "{}")
        except json.JSONDecodeError:
            parsed = {"raw": raw}
        return exc.code, parsed


def _wait_for_ipc_events(
    sock: socket.socket,
    *,
    event_types: set[str],
    token: str,
    timeout: float,
) -> list[dict[str, Any]]:
    deadline = time.time() + timeout
    observed: list[dict[str, Any]] = []
    while time.time() < deadline:
        sock.settimeout(max(0.5, min(5.0, deadline - time.time())))
        try:
            message = _read_ipc(sock)
        except TimeoutError:
            continue
        if message is None:
            continue
        if message.get("type") != "agent_event":
            continue
        event_type = str(message.get("event_type") or "")
        content = str(message.get("content") or "")
        metadata = message.get("metadata") if isinstance(message.get("metadata"), dict) else {}
        serialized = json.dumps(metadata, default=str)
        if event_type in event_types and (token in content or token in serialized):
            observed.append(message)
        if event_types <= {str(event.get("event_type") or "") for event in observed}:
            break
    return observed


def eval_idle_health_updates(http_base: str, timeout: float) -> EvaluationResult:
    name = "heartbeat_idle_health_updates"
    expectation = (
        "With no forced input, the Heartbeat idle path keeps ticking: the runtime's "
        "own HTTP /health endpoint reports an advancing timestamp and cycle_count. "
        "(Health is a Postgres document now, not a file - see nodes/health.py - so "
        "this checks liveness via HTTP rather than reading a file directly.)"
    )
    status, before = _http_json(f"{http_base}/health")
    if status != 200 or not isinstance(before, dict):
        return _fail(
            name,
            expectation,
            f"GET {http_base}/health returned status={status}",
            root_cause_area="runtime/http.py",
        )

    before_timestamp = before.get("timestamp")
    before_cycle = int(before.get("cycle_count") or 0)
    deadline = time.time() + timeout
    after = before
    while time.time() < deadline:
        time.sleep(2.0)
        status, candidate = _http_json(f"{http_base}/health")
        if status != 200 or not isinstance(candidate, dict):
            continue
        after = candidate
        after_timestamp = after.get("timestamp")
        after_cycle = int(after.get("cycle_count") or 0)
        if after_timestamp != before_timestamp and after_cycle >= before_cycle:
            return _pass(
                name,
                expectation,
                "runtime /health advanced during idle heartbeat",
                before_timestamp=before_timestamp,
                after_timestamp=after_timestamp,
                before_cycle=before_cycle,
                after_cycle=after_cycle,
            )
    return _fail(
        name,
        expectation,
        "runtime /health did not visibly advance before timeout",
        root_cause_area="nodes/health.py, graph/heartbeat_graph.py, or runtime/heartbeat.py",
        before=before,
        after=after,
    )


def eval_queued_input_heartbeat_flow(
    host: str,
    port: int,
    state_dir: Path,
    timeout: float,
) -> EvaluationResult:
    name = "heartbeat_queued_input_cycle_flow"
    token = f"HB_QUEUE_{uuid.uuid4().hex[:8]}"
    expectation = (
        "A real queued runtime input is accepted through the Heartbeat queue, processed by the "
        "dequeue/setup/cycle path, and surfaced back to connected IPC clients as an agent_message "
        "callback."
    )
    current_state_path = state_dir / "current_state.json"
    before_state = _read_json(current_state_path, {})
    before_cycle = (
        int(before_state.get("cycle_count") or 0) if isinstance(before_state, dict) else 0
    )

    try:
        with _open_ipc(host, port, timeout) as sock:
            _ipc_assistant_response(
                host,
                port,
                max(timeout, 120.0),
                (
                    "Use the enqueue_background_task tool. "
                    f"request should be 'Reply exactly {token}'. "
                    "notify_on_completion should be true. "
                    "completion_event_type should be 'agent_message'. "
                    "isolated_thread should be true."
                ),
                metadata={"source": "heartbeat_live_evaluation", "mode": "queued_input_cycle"},
            )
            observed = _wait_for_ipc_events(
                sock,
                event_types={"agent_message"},
                token=token,
                timeout=timeout,
            )
    except Exception as exc:
        return _fail(
            name,
            expectation,
            f"queued input flow failed: {exc}",
            root_cause_area="tools/enqueue_background_task.py, nodes/intake.py, nodes/cycle.py, or ipc/server.py",
        )

    after_cycle = before_cycle
    after_state = before_state if isinstance(before_state, dict) else {}
    deadline = time.time() + 10.0
    while time.time() < deadline:
        candidate = _read_json(current_state_path, {})
        if isinstance(candidate, dict):
            after_state = candidate
            after_cycle = int(candidate.get("cycle_count") or 0)
            if after_cycle > before_cycle:
                break
        time.sleep(0.5)

    if observed and after_cycle > before_cycle:
        return _pass(
            name,
            expectation,
            "queued runtime input was processed by Heartbeat and emitted an IPC agent callback",
            token=token,
            before_cycle=before_cycle,
            after_cycle=after_cycle,
            observed_events=[str(event.get("event_type") or "") for event in observed],
            metadata_sources=[
                str((event.get("metadata") or {}).get("source") or "")
                for event in observed
                if isinstance(event.get("metadata"), dict)
            ],
        )

    return _fail(
        name,
        expectation,
        "queued runtime input did not complete with the expected IPC callback/cycle advance",
        root_cause_area="tools/enqueue_background_task.py, nodes/intake.py, nodes/cycle.py, or ipc/server.py",
        token=token,
        before_cycle=before_cycle,
        after_cycle=after_cycle,
        observed_events=observed,
        last_cycle_response=after_state.get("last_cycle_response")
        if isinstance(after_state, dict)
        else None,
    )


def eval_due_schedule_heartbeat_flow(
    host: str,
    port: int,
    config_dir: Path,
    state_dir: Path,
    timeout: float,
) -> EvaluationResult:
    name = "heartbeat_due_schedule_flow"
    token = f"HB_SCHEDULE_{uuid.uuid4().hex[:8]}"
    schedule_id = f"hb_eval_{uuid.uuid4().hex[:12]}"
    expectation = (
        "A due persisted schedule entry is picked up by the Heartbeat idle scheduler, "
        "enqueued into the input queue, processed, removed, checkpointed, and surfaced via IPC events."
    )
    schedule_path = config_dir / "schedule.json"
    original_schedule = _read_json(schedule_path, [])
    if not isinstance(original_schedule, list):
        original_schedule = []
    before_state = _read_json(state_dir / "current_state.json", {})
    before_checkpoint = (
        before_state.get("last_checkpoint") if isinstance(before_state, dict) else None
    )
    before_cycle = (
        int(before_state.get("cycle_count") or 0) if isinstance(before_state, dict) else 0
    )
    due_entry = {
        "id": schedule_id,
        "message": f"Reply exactly {token}.",
        "user_request": f"Live Heartbeat schedule evaluation {token}",
        "scheduled_at": "2000-01-01T00:00:00+00:00",
        "last_run": None,
        "created_at": "2000-01-01T00:00:00+00:00",
    }

    with _open_ipc(host, port, timeout) as sock:
        _write_json(schedule_path, [*original_schedule, due_entry])
        observed = _wait_for_ipc_events(
            sock,
            event_types={"scheduled_task_triggered", "scheduled_response", "work_completed"},
            token=token,
            timeout=timeout,
        )

    event_types = {str(event.get("event_type") or "") for event in observed}
    current_schedule = _read_json(schedule_path, [])
    schedule_removed = not (
        isinstance(current_schedule, list)
        and any(
            isinstance(entry, dict) and entry.get("id") == schedule_id for entry in current_schedule
        )
    )
    after_state: dict[str, Any] = {}
    after_checkpoint: Any = None
    after_cycle = before_cycle
    checkpoint_deadline = time.time() + 10.0
    while time.time() < checkpoint_deadline:
        candidate = _read_json(state_dir / "current_state.json", {})
        if isinstance(candidate, dict):
            after_state = candidate
            after_checkpoint = after_state.get("last_checkpoint")
            after_cycle = int(after_state.get("cycle_count") or 0)
            if after_checkpoint != before_checkpoint or after_cycle > before_cycle:
                break
        time.sleep(0.5)

    required_events = {"scheduled_task_triggered", "scheduled_response", "work_completed"}
    if (
        required_events <= event_types
        and schedule_removed
        and (after_checkpoint != before_checkpoint or after_cycle > before_cycle)
    ):
        return _pass(
            name,
            expectation,
            "due schedule was picked up and completed by Heartbeat",
            token=token,
            schedule_id=schedule_id,
            observed_events=sorted(event_types),
            before_cycle=before_cycle,
            after_cycle=after_cycle,
        )
    return _fail(
        name,
        expectation,
        "due schedule did not complete with all expected Heartbeat side effects",
        root_cause_area="nodes/schedule.py, schedule/dispatch.py, nodes/cycle.py, or nodes/checkpoint.py",
        token=token,
        schedule_id=schedule_id,
        observed_events=sorted(event_types),
        schedule_removed=schedule_removed,
        before_checkpoint=before_checkpoint,
        after_checkpoint=after_checkpoint,
        before_cycle=before_cycle,
        after_cycle=after_cycle,
    )


def eval_recurring_schedule_heartbeat_flow(
    host: str,
    port: int,
    config_dir: Path,
    timeout: float,
) -> EvaluationResult:
    name = "heartbeat_recurring_schedule_flow"
    token = f"HB_RECURRING_{uuid.uuid4().hex[:8]}"
    schedule_id = f"hb_recurring_{uuid.uuid4().hex[:12]}"
    expectation = (
        "A due recurring schedule entry is picked up by Heartbeat, emits scheduled completion events, "
        "remains in schedule.json, and gets its last_run updated."
    )
    schedule_path = config_dir / "schedule.json"
    original_schedule = _read_json(schedule_path, [])
    if not isinstance(original_schedule, list):
        original_schedule = []

    due_entry = {
        "id": schedule_id,
        "message": f"Reply exactly {token}.",
        "user_request": f"Recurring Heartbeat schedule evaluation {token}",
        "cron_expression": "*/1 * * * *",
        "last_run": None,
        "created_at": "2000-01-01T00:00:00+00:00",
    }

    with _open_ipc(host, port, timeout) as sock:
        _write_json(schedule_path, [*original_schedule, due_entry])
        observed = _wait_for_ipc_events(
            sock,
            event_types={"scheduled_task_triggered", "scheduled_response", "work_completed"},
            token=token,
            timeout=timeout,
        )

    event_types = {str(event.get("event_type") or "") for event in observed}
    schedule_entry = None
    poll_deadline = time.time() + 10.0
    while time.time() < poll_deadline:
        current_schedule = _read_json(schedule_path, [])
        if isinstance(current_schedule, list):
            schedule_entry = next(
                (
                    entry
                    for entry in current_schedule
                    if isinstance(entry, dict) and entry.get("id") == schedule_id
                ),
                None,
            )
            if isinstance(schedule_entry, dict) and schedule_entry.get("last_run"):
                break
        time.sleep(0.5)

    try:
        if (
            {"scheduled_task_triggered", "scheduled_response", "work_completed"} <= event_types
            and isinstance(schedule_entry, dict)
            and schedule_entry.get("last_run")
            and schedule_entry.get("cron_expression") == due_entry["cron_expression"]
        ):
            return _pass(
                name,
                expectation,
                "recurring schedule was executed and retained with last_run updated",
                token=token,
                schedule_id=schedule_id,
                observed_events=sorted(event_types),
                last_run=schedule_entry.get("last_run"),
            )

        return _fail(
            name,
            expectation,
            "recurring schedule did not retain/update as expected after execution",
            root_cause_area="schedule/dispatch.py, schedule/engine.py, or nodes/schedule.py",
            token=token,
            schedule_id=schedule_id,
            observed_events=sorted(event_types),
            schedule_entry=schedule_entry,
        )
    finally:
        current_schedule = _read_json(schedule_path, [])
        if isinstance(current_schedule, list):
            cleaned = [entry for entry in current_schedule if entry.get("id") != schedule_id]
            if cleaned != current_schedule:
                _write_json(schedule_path, cleaned)


def eval_a2a_callback_heartbeat_flow(
    host: str,
    port: int,
    http_base: str,
    state_dir: Path,
    timeout: float,
) -> EvaluationResult:
    name = "heartbeat_a2a_completion_flow"
    token = f"HB_A2A_{uuid.uuid4().hex[:8]}"
    task_id = f"hb-a2a-{uuid.uuid4().hex[:12]}"
    expectation = (
        "A terminal A2A HTTP callback is queued into Heartbeat, applied to conversation state, "
        "and notified through IPC/event or persisted notification state."
    )
    payload = {
        "kind": "task",
        "id": task_id,
        "contextId": f"ctx-{task_id}",
        "status": {
            "state": "completed",
            "message": {"parts": [{"kind": "text", "text": f"Heartbeat A2A completed {token}"}]},
        },
        "metadata": {"source": "heartbeat_live_evaluation", "token": token},
    }
    try:
        with _open_ipc(host, port, timeout) as sock:
            status, body = _http_json(
                f"{http_base}/a2a/tasks/callback?agent=heartbeat_live_eval",
                method="POST",
                payload=payload,
                timeout=10.0,
            )
            if status != 200 or body.get("success") is not True:
                return _fail(
                    name,
                    expectation,
                    f"callback was not accepted: status={status}, body={body}",
                    root_cause_area="runtime/http.py or a2a/tasks.py",
                    body=body,
                )
            observed = _wait_for_ipc_events(
                sock,
                event_types={"agent_message"},
                token=token,
                timeout=timeout,
            )
    except Exception as exc:
        return _fail(
            name,
            expectation,
            f"A2A callback flow failed: {exc}",
            root_cause_area="runtime/http.py, nodes/a2a.py, or runtime/queues.py",
        )

    task_records = _read_json(state_dir / "a2a_tasks.json", [])
    matching = None
    if isinstance(task_records, list):
        matching = next(
            (
                record
                for record in task_records
                if isinstance(record, dict) and record.get("task_id") == task_id
            ),
            None,
        )
    current_state = _read_json(state_dir / "current_state.json", {})
    state_text = json.dumps(current_state, default=str)
    notification_sent = bool(isinstance(matching, dict) and matching.get("notification_sent_at"))
    event_seen = bool(observed)
    message_recorded = token in state_text
    if (
        isinstance(matching, dict)
        and matching.get("status") == "completed"
        and (event_seen or notification_sent or message_recorded)
    ):
        return _pass(
            name,
            expectation,
            "terminal A2A callback was processed by Heartbeat",
            task_id=task_id,
            token=token,
            event_seen=event_seen,
            notification_sent=notification_sent,
            message_recorded=message_recorded,
        )
    return _fail(
        name,
        expectation,
        "callback persisted, but Heartbeat completion side effects were not observed",
        root_cause_area="nodes/a2a.py or a2a/tasks.py",
        task_id=task_id,
        token=token,
        matching_record=matching,
        observed_events=observed,
        message_recorded=message_recorded,
    )


def eval_async_a2a_task_heartbeat_flow(
    host: str,
    port: int,
    http_base: str,
    config_dir: Path,
    state_dir: Path,
    timeout: float,
) -> EvaluationResult:
    name = "heartbeat_async_a2a_task_flow"
    token = f"HB_ASYNC_A2A_{uuid.uuid4().hex[:8]}"
    agent_name = f"heartbeat_eval_agent_{uuid.uuid4().hex[:8]}"
    expectation = (
        "A tracked async call_agent task is submitted against a registered A2A agent and then "
        "completed by the Heartbeat a2a_poll/apply/notify loop."
    )
    registry_path = config_dir / "agents.json"
    original_registry = _read_json(registry_path, {})
    if not isinstance(original_registry, dict):
        original_registry = {}

    process: subprocess.Popen[bytes] | None = None
    task_id: str | None = None
    cleanup_needed = False
    try:
        process, test_port = _start_test_a2a_agent(timeout=20.0)
        base_url = f"http://127.0.0.1:{test_port}"
        updated_registry = dict(original_registry)
        updated_registry[agent_name] = {
            "kind": "a2a_agent",
            "description": "Temporary eval agent for Heartbeat async task testing",
            "version": "0.1.0",
            "capabilities": ["echo_async_completion"],
            "transport": "http",
            "url": base_url,
            "agent_card_url": f"{base_url}/.well-known/agent-card.json",
            "status": "stopped",
        }
        _write_json(registry_path, updated_registry)

        with _open_ipc(host, port, timeout) as event_sock:
            prompt = (
                f"Use the call_agent tool with agent '{agent_name}', request 'Complete exactly {token}', "
                "and blocking false. Reply briefly when the task is submitted."
            )
            _ipc_assistant_response(
                host,
                port,
                max(timeout, 120.0),
                prompt,
                metadata={"source": "heartbeat_live_evaluation", "mode": "async_a2a_call"},
            )

            record = None
            record_deadline = time.time() + 20.0
            while time.time() < record_deadline:
                tasks = _read_json(state_dir / "a2a_tasks.json", [])
                if isinstance(tasks, list):
                    record = next(
                        (
                            entry
                            for entry in tasks
                            if isinstance(entry, dict)
                            and entry.get("agent_name") == agent_name
                            and token in str(entry.get("request_text") or "")
                        ),
                        None,
                    )
                    if isinstance(record, dict):
                        task_id = str(record.get("task_id") or "") or None
                        cleanup_needed = task_id is not None
                        break
                time.sleep(0.5)
            if task_id is None:
                return _fail(
                    name,
                    expectation,
                    "call_agent did not persist a tracked A2A task record for the temp agent",
                    root_cause_area="tools/call_agent.py or a2a/tasks.py",
                )

            observed = _wait_for_ipc_events(
                event_sock,
                event_types={"agent_message"},
                token=token,
                timeout=timeout,
            )

        final_record = None
        final_deadline = time.time() + 15.0
        while time.time() < final_deadline:
            tasks = _read_json(state_dir / "a2a_tasks.json", [])
            if isinstance(tasks, list):
                final_record = next(
                    (
                        entry
                        for entry in tasks
                        if isinstance(entry, dict) and entry.get("task_id") == task_id
                    ),
                    None,
                )
                if (
                    isinstance(final_record, dict)
                    and final_record.get("status") == "completed"
                    and final_record.get("notification_sent_at")
                ):
                    break
            time.sleep(0.5)

        current_state = _read_json(state_dir / "current_state.json", {})
        state_text = json.dumps(current_state, default=str)
        if (
            isinstance(final_record, dict)
            and final_record.get("status") == "completed"
            and final_record.get("notification_sent_at")
            and (observed or token in state_text)
        ):
            cleanup_needed = False
            return _pass(
                name,
                expectation,
                "tracked async A2A task was completed and notified by Heartbeat",
                task_id=task_id,
                agent_name=agent_name,
                event_seen=bool(observed),
                message_recorded=token in state_text,
            )

        return _fail(
            name,
            expectation,
            "tracked async A2A task did not complete with Heartbeat notification side effects",
            root_cause_area="nodes/a2a.py, tools/call_agent.py, or a2a/tasks.py",
            task_id=task_id,
            agent_name=agent_name,
            final_record=final_record,
            observed_events=observed,
            message_recorded=token in state_text,
        )
    finally:
        if cleanup_needed and task_id:
            _http_json(
                f"{http_base}/a2a/tasks/callback?agent={agent_name}",
                method="POST",
                payload={
                    "kind": "task",
                    "id": task_id,
                    "contextId": f"ctx-{task_id}",
                    "status": {
                        "state": "completed",
                        "message": {
                            "parts": [
                                {
                                    "kind": "text",
                                    "text": f"Cleanup completion for {task_id}",
                                }
                            ]
                        },
                    },
                },
                timeout=10.0,
            )
        if task_id:
            tasks = _read_json(state_dir / "a2a_tasks.json", [])
            if isinstance(tasks, list):
                cleaned_tasks = [
                    entry
                    for entry in tasks
                    if not (isinstance(entry, dict) and entry.get("task_id") == task_id)
                ]
                if cleaned_tasks != tasks:
                    _write_json(state_dir / "a2a_tasks.json", cleaned_tasks)
        _write_json(registry_path, original_registry)
        _stop_process(process)


def run(args: argparse.Namespace) -> list[EvaluationResult]:
    return [
        eval_idle_health_updates(args.http_base, args.idle_timeout),
        eval_queued_input_heartbeat_flow(
            args.ipc_host,
            args.ipc_port,
            args.state_dir,
            args.heartbeat_timeout,
        ),
        eval_due_schedule_heartbeat_flow(
            args.ipc_host,
            args.ipc_port,
            args.config_dir,
            args.state_dir,
            args.heartbeat_timeout,
        ),
        eval_recurring_schedule_heartbeat_flow(
            args.ipc_host,
            args.ipc_port,
            args.config_dir,
            args.heartbeat_timeout,
        ),
        eval_a2a_callback_heartbeat_flow(
            args.ipc_host,
            args.ipc_port,
            args.http_base,
            args.state_dir,
            args.heartbeat_timeout,
        ),
        eval_async_a2a_task_heartbeat_flow(
            args.ipc_host,
            args.ipc_port,
            args.http_base,
            args.config_dir,
            args.state_dir,
            args.heartbeat_timeout,
        ),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--http-base", default=DEFAULT_HTTP_BASE)
    parser.add_argument("--ipc-host", default=DEFAULT_IPC_HOST)
    parser.add_argument("--ipc-port", type=int, default=DEFAULT_IPC_PORT)
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE_DIR)
    parser.add_argument("--config-dir", type=Path, default=DEFAULT_CONFIG_DIR)
    parser.add_argument("--idle-timeout", type=float, default=45.0)
    parser.add_argument("--heartbeat-timeout", type=float, default=180.0)
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON results.")
    args = parser.parse_args()

    results = run(args)
    payload = {
        "target": {
            "http_base": args.http_base,
            "ipc": f"{args.ipc_host}:{args.ipc_port}",
            "state_dir": str(args.state_dir),
            "config_dir": str(args.config_dir),
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
