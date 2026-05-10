#!/usr/bin/env python3
"""Run repeatable live evaluations against a running Progenitor instance."""

from __future__ import annotations

import argparse
import json
import socket
import struct
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


def _http_json(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    raw_body: bytes | None = None,
    timeout: float = 10.0,
) -> tuple[int, dict[str, Any]]:
    body = raw_body
    headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload).encode(ENCODING)
        headers["Content-Type"] = "application/json"
    elif raw_body is not None:
        headers["Content-Type"] = "application/json"

    req = request.Request(url, data=body, method=method, headers=headers)
    try:
        with request.urlopen(req, timeout=timeout) as response:
            data = response.read()
            return response.status, json.loads(data.decode(ENCODING) or "{}")
    except error.HTTPError as exc:
        data = exc.read()
        try:
            parsed = json.loads(data.decode(ENCODING) or "{}")
        except json.JSONDecodeError:
            parsed = {"raw": data.decode(ENCODING, errors="replace")}
        return exc.code, parsed


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


def eval_http_health(http_base: str) -> EvaluationResult:
    name = "http_health"
    expectation = "GET /health returns 200 with status=ok, service=progenitor, and queue stats."
    try:
        status, body = _http_json(f"{http_base}/health")
    except Exception as exc:
        return _fail(name, expectation, f"request failed: {exc}", root_cause_area="runtime/http.py")

    queues = body.get("queues")
    if (
        status == 200
        and body.get("status") == "ok"
        and body.get("service") == "progenitor"
        and queues
    ):
        return _pass(name, expectation, "health endpoint returned expected payload", status=status)
    return _fail(
        name,
        expectation,
        f"unexpected status/body shape: status={status}, keys={sorted(body)}",
        root_cause_area="runtime/http.py",
        body=body,
    )


def eval_agent_card(http_base: str) -> EvaluationResult:
    name = "agent_card"
    expectation = (
        "GET /.well-known/agent-card.json advertises Progenitor and A2A callback endpoint."
    )
    try:
        status, body = _http_json(f"{http_base}/.well-known/agent-card.json")
    except Exception as exc:
        return _fail(name, expectation, f"request failed: {exc}", root_cause_area="runtime/http.py")

    endpoints = body.get("endpoints") if isinstance(body, dict) else {}
    callback = endpoints.get("a2a_task_callback") if isinstance(endpoints, dict) else None
    if (
        status == 200
        and body.get("name") == "progenitor"
        and str(callback or "").endswith("/a2a/tasks/callback")
    ):
        return _pass(
            name, expectation, "agent card returned expected Progenitor metadata", status=status
        )
    return _fail(
        name,
        expectation,
        f"unexpected status/body shape: status={status}, callback={callback!r}",
        root_cause_area="runtime/http.py",
        body=body,
    )


def eval_invalid_callback(http_base: str) -> EvaluationResult:
    name = "a2a_callback_invalid_json"
    expectation = "POST /a2a/tasks/callback rejects invalid JSON with HTTP 400 and success=false."
    try:
        status, body = _http_json(
            f"{http_base}/a2a/tasks/callback",
            method="POST",
            raw_body=b"{not-json",
        )
    except Exception as exc:
        return _fail(name, expectation, f"request failed: {exc}", root_cause_area="runtime/http.py")

    if status == 400 and body.get("success") is False:
        return _pass(name, expectation, "invalid callback rejected as expected", status=status)
    return _fail(
        name,
        expectation,
        f"unexpected invalid-callback response: status={status}, body={body}",
        root_cause_area="runtime/http.py",
        body=body,
    )


def eval_valid_callback(http_base: str, state_dir: Path) -> EvaluationResult:
    name = "a2a_callback_records_terminal_task"
    expectation = "Terminal A2A callback is accepted and persisted to data/state/a2a_tasks.json."
    task_id = f"live-eval-{uuid.uuid4().hex[:12]}"
    payload = {
        "kind": "task",
        "id": task_id,
        "contextId": f"ctx-{task_id}",
        "status": {
            "state": "completed",
            "message": {"parts": [{"kind": "text", "text": "Live evaluation callback completed."}]},
        },
        "metadata": {"source": "live_evaluation"},
    }
    try:
        status, body = _http_json(
            f"{http_base}/a2a/tasks/callback?agent=live_eval_agent",
            method="POST",
            payload=payload,
        )
    except Exception as exc:
        return _fail(name, expectation, f"request failed: {exc}", root_cause_area="runtime/http.py")

    if status != 200 or body.get("success") is not True or body.get("task_id") != task_id:
        return _fail(
            name,
            expectation,
            f"callback response did not match: status={status}, body={body}",
            root_cause_area="runtime/http.py",
            body=body,
        )

    task_file = state_dir / "a2a_tasks.json"
    for _ in range(20):
        record = _find_task_record(task_file, task_id)
        if record:
            if (
                record.get("status") == "completed"
                and record.get("agent_name") == "live_eval_agent"
            ):
                return _pass(
                    name,
                    expectation,
                    "callback accepted and task record persisted",
                    status=status,
                    task_id=task_id,
                )
            return _fail(
                name,
                expectation,
                f"task persisted with unexpected fields: {record}",
                root_cause_area="state/a2a_store.py",
                task_id=task_id,
            )
        time.sleep(0.25)

    return _fail(
        name,
        expectation,
        f"task {task_id} not found in {task_file}",
        root_cause_area="state/a2a_store.py",
        task_id=task_id,
    )


def _find_task_record(task_file: Path, task_id: str) -> dict[str, Any] | None:
    if not task_file.exists():
        return None
    try:
        rows = json.loads(task_file.read_text(encoding=ENCODING))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(rows, list):
        return None
    for row in rows:
        if isinstance(row, dict) and row.get("task_id") == task_id:
            return row
    return None


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
        with _open_ipc(host, port, timeout) as sock:
            _write_ipc(
                sock,
                {
                    "type": "user_message",
                    "content": prompt,
                    "request_id": f"live-eval-{uuid.uuid4().hex[:8]}",
                    "metadata": {"source": "live_evaluation", "mode": "non_streaming"},
                },
            )
            while True:
                msg = _read_ipc(sock)
                if msg is None:
                    raise RuntimeError("connection closed before assistant response")
                if msg.get("type") == "agent_event":
                    continue
                if msg.get("type") == "error":
                    return _fail(
                        name,
                        expectation,
                        f"IPC returned error: {msg.get('content')}",
                        root_cause_area="ipc/server.py or agent/agent.py",
                        message=msg,
                    )
                if msg.get("type") == "assistant_message":
                    content = str(msg.get("content") or "")
                    if marker in content:
                        return _pass(
                            name, expectation, "assistant_message contained expected marker"
                        )
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
        with _open_ipc(host, port, timeout) as sock:
            _write_ipc(
                sock,
                {
                    "type": "user_message",
                    "content": prompt,
                    "stream": True,
                    "request_id": f"live-eval-stream-{uuid.uuid4().hex[:8]}",
                    "metadata": {"source": "live_evaluation", "mode": "streaming"},
                },
            )
            while True:
                msg = _read_ipc(sock)
                if msg is None:
                    raise RuntimeError("connection closed before final stream chunk")
                msg_type = msg.get("type")
                if msg_type == "agent_event":
                    continue
                if msg_type == "stream_event":
                    event_types.append(str(msg.get("event_type") or "unknown"))
                    continue
                if msg_type == "error":
                    return _fail(
                        name,
                        expectation,
                        f"IPC returned error: {msg.get('content')}",
                        root_cause_area="ipc/server.py or ipc/deepagent_stream.py",
                        message=msg,
                    )
                if msg_type == "stream_chunk":
                    chunks.append(str(msg.get("content") or ""))
                    if msg.get("is_final") is True:
                        final_seen = True
                        break
                elif msg_type == "stream_end":
                    final_seen = True
                    break
                elif msg_type == "assistant_message":
                    chunks.append(str(msg.get("content") or ""))
                    final_seen = True
                    break
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


def run(args: argparse.Namespace) -> list[EvaluationResult]:
    results = [
        eval_http_health(args.http_base),
        eval_agent_card(args.http_base),
        eval_invalid_callback(args.http_base),
    ]
    if not args.skip_mutating_callback:
        results.append(eval_valid_callback(args.http_base, args.state_dir))
    results.append(eval_ipc_heartbeat(args.ipc_host, args.ipc_port, args.ipc_timeout))
    if not args.skip_llm:
        results.append(eval_ipc_non_streaming(args.ipc_host, args.ipc_port, args.llm_timeout))
        results.append(eval_ipc_streaming(args.ipc_host, args.ipc_port, args.llm_timeout))
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--http-base", default=DEFAULT_HTTP_BASE)
    parser.add_argument("--ipc-host", default=DEFAULT_IPC_HOST)
    parser.add_argument("--ipc-port", type=int, default=DEFAULT_IPC_PORT)
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE_DIR)
    parser.add_argument("--ipc-timeout", type=float, default=10.0)
    parser.add_argument("--llm-timeout", type=float, default=180.0)
    parser.add_argument("--skip-llm", action="store_true")
    parser.add_argument("--skip-mutating-callback", action="store_true")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON results.")
    args = parser.parse_args()

    results = run(args)
    payload = {
        "target": {
            "http_base": args.http_base,
            "ipc": f"{args.ipc_host}:{args.ipc_port}",
            "state_dir": str(args.state_dir),
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
