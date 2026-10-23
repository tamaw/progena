"""Declarative graph-agent DSL backing the Routines page.

A routine is authored as a JSON graph (see ``data/schemas/graph.routine.schema.v1.json``)
of ``prompt``/restricted-expression ``eval``/sandboxed ``python``/``condition``/
``classify``/``agent``/``a2a``/``routine``/``noop`` nodes connected by plain or
``state_equals``-routed edges, and compiled by
:mod:`src.progena.routines.factory` into a runnable ``langgraph.graph.StateGraph``.

Modules:
    graph: Pydantic models and structural validation for a routine graph document.
    expression: Restricted AST-based expression evaluator for ``eval``/``condition`` nodes;
        it is not used for sandboxed ``python`` nodes.
    factory: Compiles a validated ``RoutineGraph`` into a LangGraph runnable.
    store: Storage keys + parse/validate helpers shared by the runtime's
        ``/routines/`` virtual-filesystem mount and the FastAPI backend's
        document-store access to the same underlying document.
    revisions: Async readers for immutable routine graph revisions captured by a
        Postgres trigger on the ``store`` table (backend-owned DDL).
    subagent: ``CompiledSubAgent`` proxy that resolves a pinned or latest graph
        revision and compiles/caches it per revision id.
"""

from __future__ import annotations
