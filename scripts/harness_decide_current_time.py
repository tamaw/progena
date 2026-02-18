import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.progenitor.llm.client import LLMClient
from app.progenitor.runtime.decide import create_node
from app.progenitor.runtime import loop as loop_module
from app.progenitor.tools.registry import ToolRegistry


class _DummyStateManager:
    async def log_decision(self, data: dict[str, Any]) -> None:
        pass

    async def save_messages(self, messages: list[dict[str, Any]]) -> None:
        pass


class _DummyLoop:
    def __init__(self, streaming_enabled: bool) -> None:
        self._streaming_enabled = streaming_enabled
        self._response_was_streamed = False
        self._streamed_response_content = ""
        self._tokens: list[str] = []

    async def stream_token(self, token: str) -> None:
        self._tokens.append(token)

    async def _push_event(self, event_type: str, content: str, metadata: dict[str, Any] | None = None) -> None:
        return


def _preview(value: Any, max_chars: int = 1500) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, indent=2)
    except Exception:
        text = repr(value)
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n...<truncated>..."


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--input", type=str, default="curren ttime now plesae")
    parser.add_argument("--streaming", action="store_true")
    args = parser.parse_args()

    llm = LLMClient()

    original_extract = llm._extract_text_and_reasoning

    def wrapped_extract(data: dict[str, Any]) -> tuple[str, str]:
        text, reasoning = original_extract(data)
        if not text.strip():
            print("\n--- EMPTY EXTRACTION PAYLOAD ---")
            print(f"status={data.get('status')} error={data.get('error')} incomplete={data.get('incomplete_details')} truncation={data.get('truncation')}")
            print("text field:")
            print(_preview(data.get("text"), 900))
            print("output field:")
            print(_preview(data.get("output"), 1600))
            print("--- END EMPTY PAYLOAD ---\n")
        return text, reasoning

    llm._extract_text_and_reasoning = wrapped_extract  # type: ignore[method-assign]

    registry_path = Path("data/state/registry.json")
    tool_registry = ToolRegistry(registry_path)
    await tool_registry.load()

    decide_node = create_node(
        llm_client=llm,
        tool_registry=tool_registry,
        state_manager=_DummyStateManager(),
        config=None,
    )

    previous_loop = loop_module._current_loop
    dummy_loop = _DummyLoop(streaming_enabled=args.streaming)
    loop_module._current_loop = dummy_loop

    empty_decisions = 0

    try:
        for i in range(1, args.runs + 1):
            state = {
                "current_input": args.input,
                "messages": [{"role": "user", "content": args.input}],
                "backlog": [],
            }

            result = await decide_node(state)
            decision = result.get("current_decision")

            print(f"=== RUN {i}/{args.runs} ===")
            if decision is None:
                print("decision: None")
                empty_decisions += 1
                continue

            action = getattr(decision, "action", None)
            reasoning = getattr(decision, "reasoning", "")
            direct_response = getattr(decision, "direct_response", None)

            response_text = direct_response if isinstance(direct_response, str) else ""
            print(f"action: {action}")
            print(f"reasoning: {reasoning}")
            print(f"direct_response_len: {len(response_text)}")
            print(f"direct_response_preview: {response_text[:200]!r}")
            print(f"streamed_tokens: {len(dummy_loop._tokens)}")
            if not response_text.strip() and not dummy_loop._tokens:
                empty_decisions += 1
            print()

        print("=== SUMMARY ===")
        print(f"runs: {args.runs}")
        print(f"streaming_mode: {args.streaming}")
        print(f"runs_without_visible_response_text: {empty_decisions}")

    finally:
        loop_module._current_loop = previous_loop
        await llm.close()


if __name__ == "__main__":
    asyncio.run(main())
