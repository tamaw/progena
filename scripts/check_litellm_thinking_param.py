"""
Reproduce and validate "thinking" parameter behavior with OpenAI-compatible clients.

Usage:
  & .\\.venv\\Scripts\\Activate.ps1
  python scripts/check_litellm_thinking_param.py
"""

from __future__ import annotations

import asyncio
import os

from openai import AsyncOpenAI


async def main() -> int:
    base_url = os.getenv("LITELLM_BASE_URL", "http://localhost:4000")
    api_key = os.getenv("LITELLM_API_KEY", "not-needed")
    model = os.getenv("PROGENITOR_MODEL_DEFAULT", "gpt-5.2-chat")
    headers = {"x-litellm-api-key": api_key}

    client = AsyncOpenAI(base_url=base_url.rstrip("/"), api_key=api_key, default_headers=headers)
    messages = [{"role": "user", "content": "Say hello in one short sentence."}]

    print(f"Base URL: {base_url}")
    print(f"Model: {model}")
    print("1) Calling with top-level thinking=...")
    try:
        await client.chat.completions.create(
            model=model,
            messages=messages,
            thinking={"type": "enabled"},
        )
        print("   Unexpected success (top-level thinking accepted).")
    except TypeError as exc:
        print(f"   Expected TypeError: {exc}")
    except Exception as exc:
        print(f"   Non-TypeError exception: {type(exc).__name__}: {exc}")

    print("2) Calling with extra_body={thinking: ...}")
    try:
        await client.chat.completions.create(
            model=model,
            messages=messages,
            extra_body={"thinking": {"type": "enabled"}},
        )
        print("   Success.")
    except Exception as exc:
        print(f"   Exception (no top-level thinking TypeError): {type(exc).__name__}: {exc}")

    await client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
