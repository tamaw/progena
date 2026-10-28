"""Routine thread snapshots read against the graph the run was pinned to."""

from collections import OrderedDict
from types import SimpleNamespace
from typing import Any

import src.progena.routines.subagent as subagent


async def test_only_revision_pinned_graphs_are_reused(monkeypatch) -> None:
    """An unpinned run follows the live routine, which can change between reads."""
    compiled: list[str] = []

    async def load(thread_id: str, *_: Any) -> tuple[Any, None]:
        return SimpleNamespace(thread=thread_id), None

    def compile_graph(graph: Any, **_: Any) -> Any:
        compiled.append(graph.thread)

        async def aget_state(config: dict[str, Any]) -> Any:
            return SimpleNamespace(thread=config["configurable"]["thread_id"])

        return SimpleNamespace(aget_state=aget_state)

    monkeypatch.setattr(subagent, "_load_pinned_routine_graph", load)
    monkeypatch.setattr(subagent, "compile_routine_graph", compile_graph)
    cache: OrderedDict[tuple[str, str], Any] = OrderedDict()

    async def read(thread_id: str) -> Any:
        return await subagent.routine_thread_state(
            thread_id=thread_id,
            model=SimpleNamespace(),  # type: ignore[arg-type]
            state_manager=None,
            revision_reader=None,  # type: ignore[arg-type]
            agent_registry={},
            graph_cache=cache,
        )

    pinned = "routine:demo:rev1:session-a"
    unpinned = "routine:demo:unpinned:session-b"
    first = await read(pinned)
    second = await read("routine:demo:rev1:session-c")
    await read(unpinned)
    await read(unpinned)

    # Same revision, different sessions: one compile, each read its own thread.
    assert (first.thread, second.thread) == (pinned, "routine:demo:rev1:session-c")
    assert compiled == [pinned, unpinned, unpinned]
