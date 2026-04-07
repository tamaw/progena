"""DeepAgent tool adapters.

Progenitor's system tools live under `app/progenitor/tools/` as `BaseSystemTool`
implementations. The DeepAgent needs to expose these as LangChain tools so the
LLM can invoke them directly during a conversation (e.g. `current_time`).

External MCP tools are surfaced separately as dynamic StructuredTool wrappers so
DeepAgent sees the real MCP schemas instead of routing everything through a
generic proxy tool.
"""

import contextlib
import inspect
from collections.abc import Awaitable, Callable
from copy import deepcopy
from typing import Any, Literal, cast

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field, create_model

from ..mcp_tool_utils import (
    get_mcp_tool_description,
    get_mcp_tool_name,
    get_mcp_tool_parameters,
)
from ..nodes.node_helpers.registry import get_all_system_tools
from ..runtime.loop import get_current_loop
from ..tools.protocol import ToolContext
from ..tools.registry import normalize_tool_name


def _tool_model_name(name: str, suffix: str = "Input") -> str:
    base = normalize_tool_name(name) or "tool"
    return "".join(part.capitalize() for part in base.split("_")) + suffix


def _union_annotations(annotations: list[Any]) -> Any:
    unique: list[Any] = []
    seen: set[str] = set()
    for annotation in annotations:
        key = repr(annotation)
        if key in seen:
            continue
        seen.add(key)
        unique.append(annotation)

    if not unique:
        return Any
    if any(annotation is Any for annotation in unique):
        return Any

    merged = unique[0]
    for annotation in unique[1:]:
        merged = merged | annotation
    return merged


def _json_schema_to_model(schema: dict[str, Any], model_name: str) -> type[BaseModel]:
    properties = schema.get("properties")
    required = schema.get("required")
    if not isinstance(properties, dict):
        properties = {}
    required_names = (
        {str(item) for item in required if isinstance(item, str)}
        if isinstance(required, list)
        else set()
    )

    fields: dict[str, tuple[Any, Any]] = {}
    for field_name, field_schema in properties.items():
        if not isinstance(field_schema, dict):
            field_schema = {}

        annotation = _json_schema_to_annotation(
            field_schema,
            model_name=_tool_model_name(f"{model_name}_{field_name}", suffix="Field"),
        )

        if "default" in field_schema:
            default = deepcopy(field_schema["default"])
        elif field_name in required_names:
            default = ...
        else:
            default = None
            if annotation is not Any:
                annotation = annotation | None

        field_kwargs: dict[str, Any] = {}
        if isinstance(field_schema.get("description"), str):
            field_kwargs["description"] = field_schema["description"]
        if isinstance(field_schema.get("title"), str):
            field_kwargs["title"] = field_schema["title"]
        if isinstance(field_schema.get("minimum"), (int, float)):
            field_kwargs["ge"] = field_schema["minimum"]
        if isinstance(field_schema.get("maximum"), (int, float)):
            field_kwargs["le"] = field_schema["maximum"]
        if isinstance(field_schema.get("minLength"), int):
            field_kwargs["min_length"] = field_schema["minLength"]
        if isinstance(field_schema.get("maxLength"), int):
            field_kwargs["max_length"] = field_schema["maxLength"]
        if isinstance(field_schema.get("minItems"), int):
            field_kwargs["min_length"] = field_schema["minItems"]
        if isinstance(field_schema.get("maxItems"), int):
            field_kwargs["max_length"] = field_schema["maxItems"]

        fields[field_name] = (annotation, Field(default, **field_kwargs))

    extra: Literal["allow", "forbid"] = (
        "forbid" if schema.get("additionalProperties") is False else "allow"
    )
    model = create_model(
        model_name,
        __config__=ConfigDict(extra=extra),
        **cast(Any, fields),
    )
    if isinstance(schema.get("description"), str):
        model.__doc__ = schema["description"]
    return cast(type[BaseModel], model)


def _json_schema_to_annotation(schema: dict[str, Any], model_name: str) -> Any:
    if not isinstance(schema, dict):
        return Any

    for variant_key in ("anyOf", "oneOf"):
        variants = schema.get(variant_key)
        if isinstance(variants, list) and variants:
            return _union_annotations(
                [
                    _json_schema_to_annotation(
                        cast(dict[str, Any], variant) if isinstance(variant, dict) else {},
                        model_name=f"{model_name}Option{index}",
                    )
                    for index, variant in enumerate(variants, start=1)
                ]
            )

    enum_values = schema.get("enum")
    if isinstance(enum_values, list) and enum_values:
        literal_values = tuple(
            value
            for value in enum_values
            if isinstance(value, (str, int, float, bool)) and not isinstance(value, complex)
        )
        if len(literal_values) == len(enum_values):
            return cast(Any, Literal)[literal_values]

    schema_type = schema.get("type")
    if isinstance(schema_type, list) and schema_type:
        base_schema = {key: value for key, value in schema.items() if key != "type"}
        return _union_annotations(
            [
                _json_schema_to_annotation(
                    {**base_schema, "type": variant_type},
                    model_name=f"{model_name}{index}",
                )
                for index, variant_type in enumerate(schema_type, start=1)
            ]
        )

    if schema_type == "string":
        return str
    if schema_type == "integer":
        return int
    if schema_type == "number":
        return float
    if schema_type == "boolean":
        return bool
    if schema_type == "null":
        return type(None)
    if schema_type == "array":
        item_schema = (
            cast(dict[str, Any], schema.get("items"))
            if isinstance(schema.get("items"), dict)
            else {}
        )
        item_annotation = _json_schema_to_annotation(item_schema, model_name=f"{model_name}Item")
        return cast(Any, list)[item_annotation]
    if schema_type == "object" or isinstance(schema.get("properties"), dict):
        additional_properties = schema.get("additionalProperties")
        if isinstance(additional_properties, dict) and not schema.get("properties"):
            value_annotation = _json_schema_to_annotation(
                additional_properties,
                model_name=f"{model_name}Value",
            )
            return cast(Any, dict)[str, value_annotation]
        return _json_schema_to_model(schema, model_name)

    return Any


def _plain_tool_data(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(exclude_none=True)
    if isinstance(value, dict):
        return {str(key): _plain_tool_data(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [_plain_tool_data(item) for item in value]
    return value


def _wrap_mcp_tool(llm_client: Any, tool: dict[str, Any]) -> StructuredTool | None:
    name = get_mcp_tool_name(tool)
    if name is None:
        return None

    description = get_mcp_tool_description(tool)
    schema = get_mcp_tool_parameters(tool, require_object=True)
    if schema is None:
        return None
    args_schema = _json_schema_to_model(schema, _tool_model_name(name))

    async def _run_mcp_tool(**kwargs: Any) -> dict[str, Any]:
        call = getattr(llm_client, "_call_mcp_tool", None)
        if not callable(call):
            raise RuntimeError("MCP caller unavailable")
        call_typed = cast(Callable[[dict[str, Any]], Awaitable[dict[str, Any]]], call)
        arguments = cast(dict[str, Any], _plain_tool_data(kwargs))
        return await call_typed({"name": name, "arguments": arguments})

    return StructuredTool.from_function(
        coroutine=_run_mcp_tool,
        name=name,
        description=description,
        args_schema=args_schema,
        infer_schema=False,
    )


def create_dynamic_mcp_tools(llm_client: Any = None) -> list[StructuredTool]:
    """Create LangChain tools for the currently loaded external MCP tools."""
    raw_tools = getattr(llm_client, "mcp_tools", None)
    if not isinstance(raw_tools, list):
        return []

    wrapped_tools: list[StructuredTool] = []
    for tool in raw_tools:
        if not isinstance(tool, dict):
            continue
        wrapped = _wrap_mcp_tool(llm_client, tool)
        if wrapped is not None:
            wrapped_tools.append(wrapped)
    return wrapped_tools


def create_deepagent_tools(
    tool_registry: Any = None,
    llm_client: Any = None,
    state_manager: Any = None,
) -> list[StructuredTool]:
    """Create DeepAgent-compatible system tools."""

    def _wrap_system_tool(system_tool: Any) -> StructuredTool:
        async def _execute_wrapped_tool(
            request: str = "",
            tool_args: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
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

            ctx = ToolContext(
                tool_registry=tool_registry,
                llm_client=llm_client,
                state=cast(Any, state),
                state_manager=state_manager,
                user_input=request or "",
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

        async def _run(
            request: str = "", tool_args: dict[str, Any] | None = None
        ) -> dict[str, Any]:
            return await _execute_wrapped_tool(request=request, tool_args=tool_args)

        async def _run_call_agent(
            request: str = "",
            agent: str = "",
            agent_name: str = "",
            name: str = "",
            timeout_seconds: float = 60.0,
            context: dict[str, Any] | None = None,
            tool_args: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            merged_args: dict[str, Any] = dict(tool_args or {})
            merged_args.setdefault("agent", agent)
            merged_args.setdefault("agent_name", agent_name)
            merged_args.setdefault("name", name)
            merged_args.setdefault("request", request)
            merged_args.setdefault("timeout_seconds", timeout_seconds)
            if isinstance(context, dict) and context:
                merged_args.setdefault("context", context)
            return await _execute_wrapped_tool(request=request, tool_args=merged_args)

        coroutine_fn = (
            _run_call_agent if str(getattr(system_tool, "name", "")) == "call_agent" else _run
        )

        return StructuredTool.from_function(
            coroutine=coroutine_fn,
            name=str(getattr(system_tool, "name", "system_tool")),
            description=str(getattr(system_tool, "description", "Progenitor system tool")),
        )

    return [_wrap_system_tool(system_tool) for system_tool in get_all_system_tools()]
