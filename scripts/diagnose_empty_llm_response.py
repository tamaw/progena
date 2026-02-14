import asyncio
import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.progenitor.llm.client import LLMClient
from app.progenitor.llm.prompts import PromptTemplates


def _compose_user_response_system_prompt(genesis_prompt: str, extra_prompt: str | None = None) -> str | None:
    base = (genesis_prompt or "").strip()
    extra = (extra_prompt or "").strip()
    if base and extra:
        return f"{base}\n\n{extra}"
    if base:
        return base
    return extra or None


def _preview(value: Any, max_chars: int = 1200) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, indent=2)
    except Exception:
        text = repr(value)
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n...<truncated>..."


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=1)
    args = parser.parse_args()

    prompts = PromptTemplates()
    llm = LLMClient()

    user_input = "current time again please"
    context_label = "current_time"
    context_data = "Current local time is 2026-02-13 16:12:42"

    system_prompt = _compose_user_response_system_prompt(
        prompts.get_genesis_prompt(),
        prompts.get(
            "decide.tool_response_system.md",
            context_label=context_label,
            context_data=context_data,
        ),
    )

    messages = [{"role": "user", "content": user_input}]

    empty_count = 0
    for i in range(1, max(1, args.runs) + 1):
        input_messages = llm._build_input_messages(messages, system_prompt)
        raw = await llm._post_response(
            input_messages=input_messages,
            response_format=None,
            include_reasoning=False,
            temperature=0.7,
            max_tokens=4096,
            tools=None,
        )
        extracted_text, extracted_reasoning = llm._extract_text_and_reasoning(raw)

        if not extracted_text.strip():
            empty_count += 1

        print(f"=== RUN {i}/{args.runs} ===")
        print(f"status: {raw.get('status')}")
        print(f"error: {raw.get('error')}")
        print(f"incomplete_details: {raw.get('incomplete_details')}")
        print(f"truncation: {raw.get('truncation')}")
        print(f"text_length: {len(extracted_text)}")
        print(f"reasoning_length: {len(extracted_reasoning)}")

        if not extracted_text.strip():
            print("-- EMPTY CASE DETAILS --")
            print(f"keys: {list(raw.keys())}")
            print("top-level text:")
            print(_preview(raw.get("text"), 800))
            print("output:")
            print(_preview(raw.get("output"), 1200))
        print()

    print("=== SUMMARY ===")
    print(f"runs: {args.runs}")
    print(f"empty_extracted_text: {empty_count}")

    chat_result = await llm.chat(
        messages=messages,
        system_prompt=system_prompt,
        temperature=0.7,
        max_tokens=4096,
    )
    print("=== FINAL chat() RESULT ===")
    print(f"chat_length: {len(chat_result)}")
    print(f"chat_preview: {_preview(chat_result, 400)}")

    await llm.close()


if __name__ == "__main__":
    asyncio.run(main())
