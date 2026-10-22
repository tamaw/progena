"""Deterministic fixture executed through the real object-agent protocol runner."""

from __future__ import annotations

import asyncio
from typing import Any

from langchain_core.runnables import RunnableLambda

from .tasks import HELLO_WORLD_ASYNC_RESULT


def hello_world_runnable() -> RunnableLambda:
    async def run(state: dict[str, Any]) -> dict[str, Any]:
        await asyncio.sleep(2.0)
        return {
            "messages": [
                *(state.get("messages") or []),
                {"role": "assistant", "content": HELLO_WORLD_ASYNC_RESULT},
            ]
        }

    return RunnableLambda(run)
