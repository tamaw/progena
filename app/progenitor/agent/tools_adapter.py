"""DeepAgent tool adapters.

Progenitor's system tools live under `app/progenitor/tools/` as `BaseSystemTool`
implementations. The DeepAgent needs to expose these as LangChain tools so the
LLM can invoke them directly during a conversation (e.g. `current_time`).

External MCP tools are loaded via langchain-mcp-adapters and surfaced directly
to DeepAgent as LangChain tools.
"""

import contextlib
import inspect
import re
from typing import Any, Literal, cast

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field, create_model

from ..nodes.node_helpers.registry import get_all_system_tools
from ..runtime.loop import get_current_loop
from ..tools.protocol import ToolContext


def create_dynamic_mcp_tools(llm_client: Any = None) -> list[BaseTool]:
    """Return the live MCP tools loaded through langchain-mcp-adapters."""
    raw_tools = getattr(llm_client, "mcp_langchain_tools", None)
    if not isinstance(raw_tools, list):
        return []
    return [tool for tool in raw_tools if isinstance(tool, BaseTool)]


_NON_ALNUM_RE = re.compile(r"[^0-9A-Za-z]+")


def _pascal_case(value: str) -> str:
    parts = [part for part in _NON_ALNUM_RE.split(value or "") if part]
    return "".join(part[:1].upper() + part[1:] for part in parts) or "Tool"


def _field_type_from_schema(schema: dict[str, Any], model_name: str) -> Any:
    raw_type = schema.get("type")
    json_type = raw_type[0] if isinstance(raw_type, list) and raw_type else raw_type

    enum_values = schema.get("enum")
    if isinstance(enum_values, list) and enum_values:
        return Literal.__getitem__(tuple(enum_values))

    if json_type == "string":
        return str
    if json_type == "integer":
        return int
    if json_type == "number":
        return float
    if json_type == "boolean":
        return bool
    if json_type == "array":
        items = schema.get("items")
        if isinstance(items, dict):
            _field_type_from_schema(items, f"{model_name}Item")
        return list[Any]
    if json_type == "object":
        properties = schema.get("properties")
        if isinstance(properties, dict):
            return _schema_to_args_model(model_name, schema)
        return dict[str, Any]
    return Any


def _field_info_from_schema(schema: dict[str, Any], required: bool) -> Any:
    default = ... if required and "default" not in schema else schema.get("default")

    extras = {
        key: value
        for key, value in schema.items()
        if key not in {"type", "properties", "items", "required", "default", "description", "enum"}
    }

    field_kwargs: dict[str, Any] = {
        "default": default,
        "description": schema.get("description"),
    }
    if "pattern" in extras:
        field_kwargs["pattern"] = extras.pop("pattern")
    if "minimum" in extras:
        field_kwargs["ge"] = extras.pop("minimum")
    if "maximum" in extras:
        field_kwargs["le"] = extras.pop("maximum")
    if "minLength" in extras:
        field_kwargs["min_length"] = extras.pop("minLength")
    if "maxLength" in extras:
        field_kwargs["max_length"] = extras.pop("maxLength")
    if "minItems" in extras:
        field_kwargs["min_length"] = extras.pop("minItems")
    if "maxItems" in extras:
        field_kwargs["max_length"] = extras.pop("maxItems")
    if extras:
        field_kwargs["json_schema_extra"] = extras

    return Field(**field_kwargs)


def _schema_to_args_model(model_name: str, schema: dict[str, Any]) -> type[BaseModel]:
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return create_model(model_name)

    required_fields = schema.get("required")
    required = set(required_fields) if isinstance(required_fields, list) else set()

    fields: dict[str, tuple[Any, Any]] = {}
    for field_name, field_schema in properties.items():
        if not isinstance(field_schema, dict):
            field_schema = {}
        nested_name = f"{model_name}{_pascal_case(field_name)}"
        field_type = _field_type_from_schema(field_schema, nested_name)
        field_info = _field_info_from_schema(field_schema, field_name in required)
        fields[field_name] = (field_type, field_info)

    model = cast(
        type[BaseModel],
        create_model(model_name, __base__=BaseModel, **fields),  # type: ignore[call-overload]
    )
    model.__module__ = __name__
    return model


def _args_schema_for_tool(system_tool: Any) -> type[BaseModel]:
    raw_parameters = getattr(system_tool, "parameters", None)
    if not isinstance(raw_parameters, dict):
        raw_parameters = {
            "type": "object",
            "properties": {
                "request": {
                    "type": "string",
                    "description": "Natural language request for the tool.",
                }
            },
            "required": ["request"],
        }
    model_name = f"{_pascal_case(str(getattr(system_tool, 'name', 'system_tool')))}Args"
    return _schema_to_args_model(model_name, raw_parameters)


def create_deepagent_tools(
    tool_registry: Any = None,
    llm_client: Any = None,
    state_manager: Any = None,
) -> list[StructuredTool]:
    """Create DeepAgent-compatible system tools."""

    def _wrap_system_tool(system_tool: Any) -> StructuredTool:
        async def _execute_wrapped_tool(**kwargs: Any) -> dict[str, Any]:
            # External deploy pipelines can update agents.json; reload best-effort so new
            # agents/tools show up without requiring a restart.
            load_registry = (
                getattr(tool_registry, "load", None) if tool_registry is not None else None
            )
            if callable(load_registry):
                with contextlib.suppress(Exception):
                    maybe = load_registry()
                    if inspect.isawaitable(maybe):
                        await cast(Any, maybe)

            # Prefer the live loop state when running under ProgenitorLoop so tools that
            # mutate state (e.g. new_conversation) take effect immediately.
            state = None
            loop = get_current_loop()
            if loop is not None and isinstance(getattr(loop, "_state", None), dict):
                state = loop._state
            if state_manager is not None:
                get_state = getattr(state_manager, "get_current_state", None)
                if callable(get_state) and inspect.iscoroutinefunction(get_state):
                    with contextlib.suppress(Exception):
                        state = state or await get_state()

            tool_args = dict(kwargs)
            nested_args = tool_args.get("tool_args")
            if isinstance(nested_args, dict):
                merged_args = dict(nested_args)
                merged_args.update({k: v for k, v in tool_args.items() if k != "tool_args"})
                tool_args = merged_args

            request = str(tool_args.get("request") or "")
            ctx = ToolContext(
                tool_registry=tool_registry,
                llm_client=llm_client,
                state=cast(Any, state),
                state_manager=state_manager,
                user_input=request,
                tool_args=tool_args,
            )

            result = await system_tool.execute(ctx)
            if result.success:
                # Persist/propagate state updates when provided (DeepAgent doesn't
                # automatically merge ToolResult.state_updates like our node path).
                if result.state_updates and isinstance(state, dict):
                    state.update(result.state_updates)
                    update_state = (
                        getattr(state_manager, "update_current_state", None)
                        if state_manager
                        else None
                    )
                    if callable(update_state) and inspect.iscoroutinefunction(update_state):
                        with contextlib.suppress(Exception):
                            await update_state(result.state_updates)
                return result.data or {}
            return {
                "success": False,
                "error": result.error or f"{system_tool.name} failed",
                "should_retry_slow_path": bool(getattr(result, "should_retry_slow_path", False)),
            }

        async def _run(**kwargs: Any) -> dict[str, Any]:
            return await _execute_wrapped_tool(**kwargs)

        return StructuredTool.from_function(
            coroutine=_run,
            name=str(getattr(system_tool, "name", "system_tool")),
            description=str(getattr(system_tool, "description", "Progenitor system tool")),
            args_schema=_args_schema_for_tool(system_tool),
        )

    return [_wrap_system_tool(system_tool) for system_tool in get_all_system_tools()]
