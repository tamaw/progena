"""
Quick Azure Responses API contract smoke test.

Usage:
  python scripts/flash_test_azure_responses_contract.py --reasoning
  python scripts/flash_test_azure_responses_contract.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

import httpx


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


def main() -> int:
    parser = argparse.ArgumentParser(description="Azure Responses API smoke test")
    parser.add_argument("--model", default=os.getenv("PROGENITOR_MODEL_DEFAULT", "gpt-5.2-chat"))
    parser.add_argument("--prompt", default="Reply with 'OK' only.")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--reasoning", action="store_true")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print request payload and exit without network call",
    )
    args = parser.parse_args()

    payload = _build_payload(args.model, args.prompt, args.reasoning)
    if args.dry_run:
        print(json.dumps(payload, indent=2))
        print("DRY_RUN_OK")
        return 0

    base_url = os.getenv("PROGENITOR_AZURE_BASE_URL", "").rstrip("/")
    api_key = os.getenv("PROGENITOR_AZURE_API_KEY", "")
    if not base_url or not api_key:
        print("Missing PROGENITOR_AZURE_BASE_URL or PROGENITOR_AZURE_API_KEY", file=sys.stderr)
        return 2

    endpoint = f"{base_url}/responses"
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
