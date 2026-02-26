#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from typing import Any
from urllib.parse import urlparse


def fail(message: str, body: str | None = None) -> int:
    print(message)
    if body:
        print(body)
    return 1


def normalize_base_url(base_url: str) -> str:
    parsed = urlparse(base_url)
    if parsed.hostname in {"localhost", "127.0.0.1"}:
        return "http://litellm:4000"
    return base_url.rstrip("/")


def split_response(raw: str) -> tuple[int, str]:
    lines = raw.splitlines()
    if not lines:
        return 0, ""
    try:
        status = int(lines[-1].strip())
    except ValueError:
        status = 0
    body = "\n".join(lines[:-1])
    return status, body


def call_via_docker_curl(
    method: str,
    url: str,
    api_key: str,
    payload: dict[str, Any] | None = None,
) -> tuple[int, str]:
    cmd = [
        "docker",
        "run",
        "--rm",
        "--network",
        "progenitor_network",
        "curlimages/curl:8.6.0",
        "-sS",
        "-X",
        method,
        url,
        "-H",
        f"Authorization: Bearer {api_key}",
        "-H",
        "Content-Type: application/json",
    ]
    if payload is not None:
        cmd.extend(["-d", json.dumps(payload, separators=(",", ":"))])
    cmd.extend(["-w", r"\n%{http_code}"])
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip())
    return split_response(proc.stdout)


def is_success(status: int) -> bool:
    return 200 <= status < 300


def parse_json(body: str) -> Any:
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return None


def extract_agent_id(payload: Any, agent_name: str) -> str:
    if isinstance(payload, list):
        for item in payload:
            if (
                isinstance(item, dict)
                and item.get("agent_name") == agent_name
                and isinstance(item.get("agent_id"), str)
            ):
                return item["agent_id"]
    elif isinstance(payload, dict) and isinstance(payload.get("agent_id"), str):
        return payload["agent_id"]
    return ""


def validate_agent_card(
    api_key: str,
    card_base: str,
    agent_name: str,
    agent_id: str,
) -> int:
    if not agent_id:
        return fail("No agent_id available for validation")

    candidates = [
        f"{card_base}/a2a/{agent_id}/.well-known/agent.json",
        f"{card_base}/a2a/{agent_id}/.well-known/agent-card.json",
    ]
    card_status = 0
    card_body = ""
    for card_url in candidates:
        card_status, card_body = call_via_docker_curl("GET", card_url, api_key)
        if is_success(card_status):
            break
    if not is_success(card_status):
        return fail(
            f"LiteLLM agent card fetch failed for {agent_name} ({agent_id}) ({card_status})",
            card_body,
        )

    card = parse_json(card_body)
    required_fields = [
        "name",
        "description",
        "skills",
        "defaultInputModes",
        "defaultOutputModes",
    ]
    if not isinstance(card, dict) or any(field not in card for field in required_fields):
        return fail(f"LiteLLM agent card validation failed for {agent_name} ({agent_id})", card_body)
    print(f"Validated LiteLLM agent card for {agent_name} ({agent_id})")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()

    agent_name = os.environ.get("AGENT_NAME")
    api_key = os.environ.get("LITELLM_MASTER_KEY")
    if not agent_name:
        return fail("AGENT_NAME is required")
    if not api_key:
        return fail("LITELLM_MASTER_KEY is required")

    port = os.environ.get("AGENT_PORT", "8080")
    agent_host = os.environ.get("AGENT_HOST", f"progenitor-agent-{agent_name}")
    agent_card_url = f"http://{agent_host}:{port}/.well-known/agent.json"

    register_base = normalize_base_url(os.environ.get("LITELLM_BASE_URL", "http://litellm:4000"))
    agents_url = (
        f"{register_base}/agents"
        if register_base.endswith("/v1")
        else f"{register_base}/v1/agents"
    )
    card_base = (
        register_base[:-3] if register_base.endswith("/v1") else register_base
    ).rstrip("/")

    status, body = call_via_docker_curl("GET", agents_url, api_key)
    if not is_success(status):
        return fail(f"Failed to list agents ({status})", body)
    listed = parse_json(body)
    agent_id = extract_agent_id(listed, agent_name)

    if args.validate_only:
        if not agent_id:
            return fail(f"Could not find existing agent_id for {agent_name}", body)
        return validate_agent_card(api_key, card_base, agent_name, agent_id)

    status, body = call_via_docker_curl("GET", agent_card_url, api_key)
    if not is_success(status):
        return fail(f"Failed to fetch agent card from {agent_card_url} ({status})", body)
    agent_card = parse_json(body)
    if not isinstance(agent_card, dict):
        return fail(f"Invalid agent card response from {agent_card_url}", body)

    payload = {
        "agent_name": agent_name,
        "agent_card_params": agent_card,
        "litellm_params": {"make_public": True},
    }

    if agent_id:
        patch_url = f"{agents_url}/{agent_id}"
        status, body = call_via_docker_curl("PATCH", patch_url, api_key, payload)
        if not is_success(status):
            return fail(f"LiteLLM PATCH {patch_url} failed ({status})", body)
        print(f"Patched existing agent {agent_name} ({agent_id})")
    else:
        status, body = call_via_docker_curl("POST", agents_url, api_key, payload)
        if not is_success(status):
            return fail(f"LiteLLM POST {agents_url} failed ({status})", body)
        agent_id = extract_agent_id(parse_json(body), agent_name)
        if not agent_id:
            return fail(f"Registration completed but no agent_id found for {agent_name}", body)
        print(f"Registered {agent_name} via {agents_url}")

    return validate_agent_card(api_key, card_base, agent_name, agent_id)


if __name__ == "__main__":
    sys.exit(main())
