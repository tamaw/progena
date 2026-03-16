"""
Quick Azure Responses API smoke test.

Usage:
  python scripts/flash_test_azure_responses_contract.py --reasoning
  python scripts/flash_test_azure_responses_contract.py --structured-builder-plan
  python scripts/flash_test_azure_responses_contract.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.agents.builder_agent.llm.client import LLMClient as BuilderAgentLLMClient
from app.agents.builder_agent.nodes.think import ThinkResponse
from app.agents.builder_agent.state.ideal_state import IdealState
from app.agents.builder_progenitor.client import BuilderClient

load_dotenv()


def _extract_output_text(payload: dict[str, Any]) -> str:
    parts: list[str] = []
    for item in payload.get("output", []):
        for content in item.get("content", []):
            if content.get("type") == "output_text":
                text = content.get("text")
                if isinstance(text, str):
                    parts.append(text)
    return "".join(parts).strip()


def _validate_response_shape(payload: dict[str, Any]) -> None:
    if payload.get("object") != "response":
        raise ValueError("Expected object='response'")
    if "id" not in payload:
        raise ValueError("Missing response id")
    if "model" not in payload:
        raise ValueError("Missing response model")
    output = payload.get("output")
    if not isinstance(output, list):
        raise ValueError("Expected output to be a list")


def _build_payload(model: str, prompt: str, include_reasoning: bool) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "input": [{"role": "user", "content": prompt}],
        "max_output_tokens": 256,
    }
    if include_reasoning:
        payload["reasoning"] = {"summary": "auto"}
    return payload


def _build_responses_endpoint(base_url: str) -> str:
    raw = (base_url or "").strip()
    parsed = urlsplit(raw)
    path = (parsed.path or "").rstrip("/")
    endpoint_path = path if path.endswith("/responses") else f"{path}/responses"
    return urlunsplit((parsed.scheme, parsed.netloc, endpoint_path, parsed.query, parsed.fragment))


async def _run_builder_structured_plan(request_text: str, model: str) -> int:
    previous_override = os.getenv("OVERRIDE_AGENT_PLANNER_MODEL")
    os.environ["OVERRIDE_AGENT_PLANNER_MODEL"] = model
    try:
        async with BuilderClient(role="planner") as client:
            result = await client.plan_step(request_text, {})
    except Exception as exc:
        print(f"STRUCTURED_REQUEST_FAILED: {exc}", file=sys.stderr)
        return 1
    finally:
        if previous_override is None:
            os.environ.pop("OVERRIDE_AGENT_PLANNER_MODEL", None)
        else:
            os.environ["OVERRIDE_AGENT_PLANNER_MODEL"] = previous_override

    print("STRUCTURED_PLAN_OK")
    print(f"model={client.model}")
    print(f"summary={result.plan.summary}")
    print(f"steps={len(result.plan.steps)}")
    print(f"has_reasoning={bool(result.reasoning)}")
    return 0


async def _run_builder_agent_structured_think(prompt: str, model: str) -> int:
    previous_override = os.getenv("OVERRIDE_AGENT_PLANNER_MODEL")
    os.environ["OVERRIDE_AGENT_PLANNER_MODEL"] = model
    try:
        async with BuilderAgentLLMClient(role="planner") as client:
            result = await client.chat_structured(
                messages=[{"role": "user", "content": prompt}],
                response_model=ThinkResponse,
                schema_name="think_response",
                system_prompt="You are a structured test. Return valid JSON matching the schema.",
                temperature=0.7,
            )
    except Exception as exc:
        print(f"BUILDER_AGENT_THINK_FAILED: {exc}", file=sys.stderr)
        return 1
    finally:
        if previous_override is None:
            os.environ.pop("OVERRIDE_AGENT_PLANNER_MODEL", None)
        else:
            os.environ["OVERRIDE_AGENT_PLANNER_MODEL"] = previous_override

    print("BUILDER_AGENT_THINK_OK")
    print(f"model={client.model}")
    print(f"approaches={len(result.approaches)}")
    return 0


async def _run_builder_agent_ideal_state(prompt: str, model: str) -> int:
    previous_override = os.getenv("OVERRIDE_AGENT_PLANNER_MODEL")
    os.environ["OVERRIDE_AGENT_PLANNER_MODEL"] = model
    try:
        async with BuilderAgentLLMClient(role="planner") as client:
            result = await client.chat_structured(
                messages=[{"role": "user", "content": prompt}],
                response_model=IdealState,
                schema_name="ideal_state",
                system_prompt="You are a structured test. Return valid JSON matching the schema.",
                temperature=0.3,
            )
    except Exception as exc:
        print(f"BUILDER_AGENT_IDEAL_STATE_FAILED: {exc}", file=sys.stderr)
        return 1
    finally:
        if previous_override is None:
            os.environ.pop("OVERRIDE_AGENT_PLANNER_MODEL", None)
        else:
            os.environ["OVERRIDE_AGENT_PLANNER_MODEL"] = previous_override

    print("BUILDER_AGENT_IDEAL_STATE_OK")
    print(f"model={client.model}")
    print(f"success_criteria={len(result.success_criteria)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Azure Responses API smoke test")
    parser.add_argument("--model", default=os.getenv("PROGENITOR_MODEL_DEFAULT", "gpt-5.2-chat"))
    parser.add_argument("--prompt", default="Reply with 'OK' only.")
    parser.add_argument(
        "--builder-request",
        default="Say hello and include a pr step.",
        help="Request text for the builder_progenitor structured planning path.",
    )
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--reasoning", action="store_true")
    parser.add_argument(
        "--structured-builder-plan",
        action="store_true",
        help="Exercise the same structured planning path BuilderClient.plan_step() uses.",
    )
    parser.add_argument(
        "--structured-builder-agent-think",
        action="store_true",
        help="Exercise the builder_agent ThinkResponse structured path that was returning Azure 400s.",
    )
    parser.add_argument(
        "--structured-builder-agent-ideal-state",
        action="store_true",
        help="Exercise the builder_agent IdealState structured path that was returning Azure 400s.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print request payload and exit without network call",
    )
    args = parser.parse_args()

    if args.structured_builder_plan:
        if args.dry_run:
            print(
                json.dumps(
                    {
                        "builder_request": args.builder_request,
                        "mode": "structured_builder_plan",
                        "model": args.model,
                    },
                    indent=2,
                )
            )
            print("DRY_RUN_OK")
            return 0
        import asyncio

        return asyncio.run(_run_builder_structured_plan(args.builder_request, args.model))

    if args.structured_builder_agent_think:
        if args.dry_run:
            print(
                json.dumps(
                    {
                        "prompt": args.prompt,
                        "mode": "structured_builder_agent_think",
                        "model": args.model,
                    },
                    indent=2,
                )
            )
            print("DRY_RUN_OK")
            return 0
        import asyncio

        return asyncio.run(_run_builder_agent_structured_think(args.prompt, args.model))

    if args.structured_builder_agent_ideal_state:
        if args.dry_run:
            print(
                json.dumps(
                    {
                        "prompt": args.prompt,
                        "mode": "structured_builder_agent_ideal_state",
                        "model": args.model,
                    },
                    indent=2,
                )
            )
            print("DRY_RUN_OK")
            return 0
        import asyncio

        return asyncio.run(_run_builder_agent_ideal_state(args.prompt, args.model))

    payload = _build_payload(args.model, args.prompt, args.reasoning)
    if args.dry_run:
        print(json.dumps(payload, indent=2))
        print("DRY_RUN_OK")
        return 0

    base_url = os.getenv("AZURE_OPENAI_BASE_URL", "").rstrip("/")
    api_key = os.getenv("AZURE_OPENAI_API_KEY", "")
    if not base_url or not api_key:
        print("Missing AZURE_OPENAI_BASE_URL or AZURE_OPENAI_API_KEY", file=sys.stderr)
        return 2

    endpoint = _build_responses_endpoint(base_url)
    headers = {
        "Content-Type": "application/json",
        "api-key": api_key,
    }

    try:
        with httpx.Client(timeout=args.timeout) as client:
            response = client.post(endpoint, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
    except Exception as exc:
        print(f"REQUEST_FAILED: {exc}", file=sys.stderr)
        return 1

    try:
        _validate_response_shape(data)
    except Exception as exc:
        print(f"CONTRACT_FAILED: {exc}", file=sys.stderr)
        return 1

    text = _extract_output_text(data)
    print("CONTRACT_OK")
    print(f"response_id={data.get('id')}")
    print(f"model={data.get('model')}")
    print(f"text_length={len(text)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
