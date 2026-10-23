"""Routine `code` nodes execute bounded, isolated QuickJS source."""

from __future__ import annotations

import pytest
from langchain_core.messages import HumanMessage
from langgraph.errors import NodeTimeoutError
from quickjs_rs import JSError
from quickjs_rs import TimeoutError as QuickJSTimeoutError

from src.progena.routines.factory import compile_routine_graph
from src.progena.routines.graph import validate_routine_graph_payload


def _payload(*nodes: dict, edges: list[dict] | None = None, **extra: object) -> dict:
    return {
        "id": "quickjs-test",
        "name": "QuickJS test",
        "description": "",
        "version": "2.0.0",
        "entry": nodes[0]["id"],
        "nodes": list(nodes),
        "edges": edges or [],
        **extra,
    }


def _code_node(code: str, *, output_key: str = "result") -> dict:
    return {
        "id": "code",
        "kind": "code",
        "output_key": output_key,
        "config": {"language": "javascript", "code": code},
    }


@pytest.mark.asyncio
async def test_code_node_runs_inline_javascript_with_structured_input_and_output():
    graph = validate_routine_graph_payload(
        _payload(
            _code_node(
                """
                const subtotal = input.items.reduce((sum, item) => sum + item.price, 0);
                return { subtotal, requested: input.request };
                """
            ),
            {
                "id": "after",
                "kind": "eval",
                "output_key": "after",
                "config": {"expression": "result.subtotal + 1"},
            },
            edges=[{"from": "code", "to": "after", "level": "normal"}],
            input_schema={
                "type": "object",
                "properties": {"items": {"type": "array"}},
                "required": ["items"],
            },
        )
    )

    compiled = compile_routine_graph(graph, model=object())
    result = await compiled.ainvoke(
        {
            "messages": [HumanMessage(content="sum these")],
            "items": [{"price": 19}, {"price": 23}],
        }
    )

    assert result["data"]["result"] == {"subtotal": 42, "requested": "sum these"}
    assert result["data"]["after"] == 43


@pytest.mark.parametrize(
    "config",
    [
        {"code": "return 1;"},
        {"language": "python", "code": "return 1;"},
        {"language": "javascript", "code": " \n\t "},
        {"language": "javascript", "code": "return 1;", "code_file": "step.js"},
    ],
)
def test_code_node_rejects_invalid_configuration(config: dict[str, str]):
    payload = _payload(
        {
            "id": "code",
            "kind": "code",
            "output_key": "result",
            "config": config,
        }
    )

    with pytest.raises(ValueError, match="schema validation failed"):
        validate_routine_graph_payload(payload)


@pytest.mark.asyncio
async def test_code_node_has_fresh_globals_for_each_execution():
    graph = validate_routine_graph_payload(
        _payload(
            _code_node(
                """
                const previous = globalThis.leaked ?? null;
                globalThis.leaked = "only this invocation";
                input.nested.value = 999;
                return { previous, copied: input.nested.value };
                """
            ),
            input_schema={
                "type": "object",
                "properties": {"nested": {"type": "object"}},
                "required": ["nested"],
            },
        )
    )
    compiled = compile_routine_graph(graph, model=object())

    first = await compiled.ainvoke(
        {"messages": [HumanMessage(content="first")], "nested": {"value": 1}}
    )
    second = await compiled.ainvoke(
        {"messages": [HumanMessage(content="second")], "nested": {"value": 2}}
    )

    assert first["data"]["result"] == {"previous": None, "copied": 999}
    assert second["data"]["result"] == {"previous": None, "copied": 999}
    assert first["data"]["nested"] == {"value": 1}
    assert second["data"]["nested"] == {"value": 2}


@pytest.mark.asyncio
async def test_code_node_propagates_javascript_errors_and_enforces_timeout():
    failing = validate_routine_graph_payload(_payload(_code_node('throw new Error("boom");')))
    with pytest.raises(JSError, match="boom"):
        await compile_routine_graph(failing, model=object()).ainvoke(
            {"messages": [HumanMessage(content="run")]}
        )

    unbounded = validate_routine_graph_payload(_payload(_code_node("while (true) {}")))
    with pytest.raises(QuickJSTimeoutError):
        await compile_routine_graph(unbounded, model=object()).ainvoke(
            {"messages": [HumanMessage(content="run")]}
        )


@pytest.mark.asyncio
async def test_code_node_rejects_oversized_result_inside_the_sandbox():
    graph = validate_routine_graph_payload(
        _payload(_code_node('return "x".repeat(1024 * 1024 + 1);'))
    )

    with pytest.raises(JSError, match="code result exceeds"):
        await compile_routine_graph(graph, model=object()).ainvoke(
            {"messages": [HumanMessage(content="run")]}
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code",
    [
        "return undefined;",
        "return Infinity;",
        "return NaN;",
        "return 1n;",
        "return { callback() {} };",
        "const value = {}; value.self = value; return value;",
    ],
)
async def test_code_node_rejects_non_json_results(code: str):
    graph = validate_routine_graph_payload(_payload(_code_node(code)))

    with pytest.raises(JSError):
        await compile_routine_graph(graph, model=object()).ainvoke(
            {"messages": [HumanMessage(content="run")]}
        )


@pytest.mark.asyncio
async def test_code_node_preserves_json_keys_and_exposes_no_host_capabilities():
    graph = validate_routine_graph_payload(
        _payload(
            _code_node(
                """
                return {
                  own: Object.hasOwn(input.value, "__proto__"),
                  value: input.value.__proto__.answer,
                  host: [typeof tools, typeof task, typeof fetch,
                         typeof process, typeof require, typeof document]
                };
                """
            ),
            input_schema={
                "type": "object",
                "properties": {"value": {"type": "object"}},
            },
        )
    )
    result = await compile_routine_graph(graph, model=object()).ainvoke(
        {
            "messages": [HumanMessage(content="inspect")],
            "value": {"__proto__": {"answer": 42}},
        }
    )
    assert result["data"]["result"] == {
        "own": True,
        "value": 42,
        "host": ["undefined"] * 6,
    }


@pytest.mark.asyncio
async def test_code_node_honors_a_shorter_graph_timeout():
    node = _code_node("const end = Date.now() + 250; while (Date.now() < end) {} return 42;")
    node["config"]["timeout"] = 0.02
    graph = validate_routine_graph_payload(_payload(node))
    with pytest.raises(NodeTimeoutError):
        await compile_routine_graph(graph, model=object()).ainvoke({})


@pytest.mark.asyncio
async def test_code_node_enforces_utf8_result_cap_after_guest_helper_mutation():
    graph = validate_routine_graph_payload(
        _payload(
            _code_node('String.prototype.charCodeAt.call = () => 0; return "é".repeat(600000);')
        )
    )
    with pytest.raises((JSError, ValueError)):
        await compile_routine_graph(graph, model=object()).ainvoke({})
