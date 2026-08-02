"""Declarative graph-agent DSL backing the Routines page.

A routine is authored as a JSON graph (see ``data/schemas/graph.routine.schema.v1.json``)
of ``prompt``/``code``/``eval``/``subagent``/``agent``/``noop`` nodes connected by plain or
``state_equals``-routed edges, and compiled by :mod:`app.progenitor.routines.factory` into a
runnable ``langgraph.graph.StateGraph``.

Modules:
    graph: Pydantic models and structural validation for a routine graph document.
    expression: Restricted AST-based expression evaluator for 'code'/'eval' nodes.
    factory: Compiles a validated ``RoutineGraph`` into a LangGraph runnable.
    store: Storage keys + parse/validate helpers shared by the runtime's
        ``/routines/`` virtual-filesystem mount and the FastAPI backend's
        document-store access to the same underlying document.
"""

from __future__ import annotations
