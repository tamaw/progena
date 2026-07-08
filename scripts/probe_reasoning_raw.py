#!/usr/bin/env python3
"""Probe Azure/OpenAI reasoning output using the raw Responses API.

This script bypasses the DeepAgent/LangChain stack and sends a direct HTTP
request using the same env-configured model/base URL/API key as Progenitor.
It is meant to answer two questions:

1. Are we actually sending the reasoning flags we think we are?
2. Does the provider return reasoning summaries and/or reasoning token usage?
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

import httpx

from app.progenitor.config import LLMConfig
from app.progenitor.llm.client import _build_responses_endpoint

DEFAULT_PROMPT = (
    "Solve this carefully: A train leaves at 3:15pm traveling 72 km/h. "
    "Another leaves the same station at 4:00pm traveling 96 km/h on the same route. "
    "At what time does the second train catch the first? Show the math clearly."
)


def _extract_reasoning_summaries(data: dict[str, Any]) -> list[str]:
    summaries: list[str] = []
    for item in data.get("output", []):
        if not isinstance(item, dict) or item.get("type") != "reasoning":
            continue
        summary = item.get("summary")
        if not isinstance(summary, list):
            continue
        for part in summary:
            if not isinstance(part, dict):
                continue
            text = part.get("text") or part.get("content")
            if isinstance(text, str) and text.strip():
                summaries.append(text.strip())
    return summaries


def _extract_output_text(data: dict[str, Any]) -> str:
    output_text = data.get("output_text")
    if isinstance(output_text, str):
        return output_text
    parts: list[str] = []
    for item in data.get("output", []):
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") in {"output_text", "text"}:
                text = part.get("text") or part.get("content")
                if isinstance(text, str):
                    parts.append(text)
    return "".join(parts).strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompt", default=DEFAULT_PROMPT, help="Prompt to send.")
    parser.add_argument(
        "--effort",
        default=None,
        help="Override reasoning effort (default: env/config value).",
    )
    parser.add_argument(
        "--summary",
        default=None,
        help="Override reasoning summary mode (default: env/config value).",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Override model deployment name (default: env/config value).",
    )
    parser.add_argument(
        "--max-output-tokens",
        type=int,
        default=5000,
        help="max_output_tokens to send.",
    )
    parser.add_argument(
        "--dump-full",
        action="store_true",
        help="Print the full JSON response.",
    )
    args = parser.parse_args()

    config = LLMConfig.from_env()
    endpoint = _build_responses_endpoint(config.base_url)
    model = args.model or config.model_default
    effort = args.effort or config.reasoning_effort
    summary = args.summary or config.reasoning_summary

    if not endpoint or endpoint == "/responses":
        print("ERROR: AZURE_OPENAI_BASE_URL is not configured.", file=sys.stderr)
        return 2
    if not config.api_key:
        print("ERROR: AZURE_OPENAI_API_KEY is not configured.", file=sys.stderr)
        return 2

    payload: dict[str, Any] = {
        "model": model,
        "input": [{"role": "user", "content": args.prompt}],
        "max_output_tokens": args.max_output_tokens,
        "reasoning": {
            "effort": effort,
            "summary": summary,
        },
    }

    print("=== request ===")
    print(
        json.dumps(
            {
                "endpoint": endpoint,
                "model": model,
                "reasoning": payload["reasoning"],
                "max_output_tokens": args.max_output_tokens,
            },
            indent=2,
        )
    )

    headers = {
        "Content-Type": "application/json",
        "api-key": config.api_key,
    }
    with httpx.Client(timeout=120.0, headers=headers) as client:
        response = client.post(endpoint, json=payload)

    print("\n=== response status ===")
    print(response.status_code)
    if not response.is_success:
        print(response.text)
        return 1

    data = response.json()
    summaries = _extract_reasoning_summaries(data)
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    output_tokens_details = (
        usage.get("output_tokens_details")
        if isinstance(usage.get("output_tokens_details"), dict)
        else {}
    )

    print("\n=== response summary ===")
    print(
        json.dumps(
            {
                "status": data.get("status"),
                "output_item_types": [
                    item.get("type") for item in data.get("output", []) if isinstance(item, dict)
                ],
                "reasoning_tokens": output_tokens_details.get("reasoning_tokens"),
                "has_reasoning_summary": bool(summaries),
                "reasoning_summary_count": len(summaries),
            },
            indent=2,
        )
    )

    print("\n=== assistant text preview ===")
    print(_extract_output_text(data)[:1500] or "<empty>")

    if summaries:
        print("\n=== reasoning summaries ===")
        for index, summary_text in enumerate(summaries, start=1):
            print(f"[{index}] {summary_text}\n")

    if args.dump_full:
        print("\n=== full response ===")
        print(json.dumps(data, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
