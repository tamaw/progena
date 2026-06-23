"""Azure Responses API client for Progenitor."""

import json
import logging
import re
from typing import TYPE_CHECKING, Any, TypeVar, cast
from urllib.parse import urlsplit, urlunsplit

import httpx
from pydantic import BaseModel

from ..config import LLMConfig, MCPConfig
from ..tools.user_communication import EventPusher

if TYPE_CHECKING:
    from ..mcp import MCPRuntime

logger = logging.getLogger(__name__)

TModel = TypeVar("TModel", bound=BaseModel)

# Context window sizes for common models (in tokens)
INLINE_TOOL_MARKUP_PATTERN = re.compile(r".*?", re.DOTALL)


def _build_responses_endpoint(base_url: str) -> str:
    """Build a valid /responses endpoint from either base URL or full endpoint URL."""
    raw = (base_url or "").strip()
    if not raw:
        return "/responses"

    parsed = urlsplit(raw)
    path = (parsed.path or "").rstrip("/")
    endpoint_path = path if path.endswith("/responses") else f"{path}/responses"

    return urlunsplit((parsed.scheme, parsed.netloc, endpoint_path, parsed.query, parsed.fragment))


def _close_object_schemas(node: Any) -> None:
    """Recursively enforce strict object schemas Azure Responses accepts."""
    if isinstance(node, dict):
        if "$ref" in node:
            ref = node["$ref"]
            node.clear()
            node["$ref"] = ref
            return
        if node.get("type") == "object":
            node["additionalProperties"] = False
            properties = node.get("properties")
            if isinstance(properties, dict):
                node["required"] = list(properties.keys())
        for value in node.values():
            _close_object_schemas(value)
        return
    if isinstance(node, list):
        for item in node:
            _close_object_schemas(item)


def _build_json_schema_format(response_model: Any, schema_name: str) -> dict[str, Any]:
    """Build response_format dict for structured output using Pydantic model.

    Args:
        response_model: Pydantic BaseModel class to generate schema from
        schema_name: Name for the JSON schema

    Returns:
        Dictionary in format: {"type": "json_schema", "json_schema": {...}}
    """
    schema = response_model.model_json_schema()
    _close_object_schemas(schema)

    return {
        "type": "json_schema",
        "json_schema": {
            "name": schema_name,
            "schema": schema,
            "strict": True,
        },
    }


class LLMClient:
    """Simple wrapper for Azure OpenAI /responses endpoint."""

    def __init__(
        self,
        config: LLMConfig | None = None,
        mcp_config: MCPConfig | None = None,
    ):
        """Initialize the LLM client.

        Args:
            config: LLM configuration. If None, loads from environment.
            mcp_config: MCP configuration. If None, starts empty until runtime init refreshes it.
        """
        self.config = config or LLMConfig.from_env()
        self.mcp_config = mcp_config or MCPConfig()
        self.model = self.config.model_default

        self.endpoint_url = _build_responses_endpoint(self.config.base_url)

        api_key = self.config.api_key or ""
        headers = {
            "Content-Type": "application/json",
            "api-key": api_key,
        }

        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(120.0),
            headers=headers,
        )

        # MCP runtime is initialized externally (startup)
        self._mcp_runtime: MCPRuntime | None = None

        # Optional event pusher for realtime progress updates via IPC
        self._event_pusher: EventPusher | None = None

        logger.info("LLM client configured (endpoint=%s, model=%s)", self.endpoint_url, self.model)

    def set_event_pusher(self, event_pusher: EventPusher | None) -> None:
        """Attach an event pusher used to stream progress updates to clients."""
        self._event_pusher = event_pusher

    async def _push_event(
        self,
        event_type: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if not self._event_pusher:
            return
        try:
            await self._event_pusher(event_type, content, metadata)
        except Exception:
            # Never let event delivery break the main LLM/tool flow
            logger.debug("Failed to push event %s", event_type, exc_info=True)

    async def close(self):
        """Close the HTTP client."""
        await self.client.aclose()
        await self.close_mcp_context()

    @property
    def mcp_runtime(self) -> "MCPRuntime | None":
        """Return the attached MCP runtime, if any."""
        return self._mcp_runtime

    def set_mcp_runtime(self, mcp_runtime: "MCPRuntime | None") -> None:
        """Attach the runtime-owned MCP state holder."""
        self._mcp_runtime = mcp_runtime

    async def close_mcp_context(self) -> None:
        """Close all initialized MCP clients and clear the active MCP context."""
        if self._mcp_runtime is None:
            return
        await self._mcp_runtime.close()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    def _build_input_messages(
        self,
        messages: list[dict[str, Any]],
        system_prompt: str | None = None,
    ) -> list[dict[str, Any]]:
        """Build Responses API input messages from chat history."""
        input_messages: list[dict[str, Any]] = []

        allowed_roles = {"system", "user", "assistant", "tool"}

        if system_prompt:
            input_messages.append(
                {
                    "role": "system",
                    "content": system_prompt,
                }
            )

        for msg in messages:
            role = str(msg.get("role", "user") or "user")
            if role not in allowed_roles:
                # Scheduled/background messages may use custom roles like
                # "scheduler". The OpenAI-compatible Responses API only
                # accepts standard roles, so coerce unknown roles to "user".
                role = "user"
            content = msg.get("content", "")

            if isinstance(content, list):
                if (
                    content
                    and isinstance(content[0], dict)
                    and content[0].get("type")
                    in {
                        "input_text",
                        "output_text",
                        "input_image",
                        "input_file",
                        "refusal",
                        "summary_text",
                        "tether_browsing_display",
                        "computer_screenshot",
                        "tool_call",
                        "tool_result",
                    }
                ):
                    wrapped_content: Any = content
                else:
                    text_parts = []
                    for part in content:
                        if isinstance(part, dict):
                            text_parts.append(str(part.get("text", "")))
                        elif isinstance(part, str):
                            text_parts.append(part)
                    wrapped_content = "".join(text_parts)
            else:
                wrapped_content = str(content)

            input_messages.append(
                {
                    "role": role,
                    "content": wrapped_content,
                }
            )

        return input_messages

    def _merge_tools(self, tools: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
        """Merge caller-supplied tools with MCP tools, de-duplicated by name."""
        if tools is not None and len(tools) == 0:
            return []

        merged: list[dict[str, Any]] = []
        seen_names: set[str] = set()

        for tool in tools or []:
            name = (
                tool.get("function", {}).get("name")
                if tool.get("type") == "function"
                else tool.get("name")
            )
            if name:
                seen_names.add(name)
            merged.append(tool)

        mcp_runtime = self._mcp_runtime
        for normalized in mcp_runtime.iter_openai_tools() if mcp_runtime is not None else []:
            name = normalized.get("function", {}).get("name")
            if not name or name in seen_names:
                continue
            seen_names.add(name)
            merged.append(normalized)

        return merged

    def _resolve_request_tools(
        self,
        tools: list[dict[str, Any]] | None,
        response_format: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        """Resolve effective tools for a request.

        For structured-output requests, we still include the tool list in the
        upstream payload (so the provider can see
        the full tool registry), but the call site should typically force
        tool_choice='none' to avoid tool calls breaking strict JSON outputs.
        """
        if response_format is not None and tools is None:
            # Default tools for structured outputs: include for context/contract,
            # but rely on tool_choice='none' during the call.
            return self._merge_tools(None)
        return self._merge_tools(tools)

    def _extract_tool_calls(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        """Extract tool calls from a Responses API payload.

        Tool calls can appear in:
        - data["output"] as {"type": "tool_call", ...}
        - data["output"] as {"type": "function_call", ...}
        - data["output"] > message > content as tool_call items
        - data["tool_calls"] as direct list
        """
        tool_calls: list[dict[str, Any]] = []
        tool_call_types = {"tool_call", "function_call"}

        # Check output array
        for item in data.get("output", []):
            if item.get("type") in tool_call_types:
                tool_calls.append(item)
                continue

            # Check message content for tool calls
            if item.get("type") == "message":
                content = item.get("content")
                if isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict) and part.get("type") in tool_call_types:
                            tool_calls.append(part)

        # Check top-level tool_calls
        if isinstance(data.get("tool_calls"), list):
            tool_calls.extend(data["tool_calls"])

        return tool_calls

    async def _call_mcp_tool(self, tool_call: dict[str, Any]) -> dict[str, Any]:
        """Execute a tool call against the correct MCP server for that tool."""
        if self._mcp_runtime is None:
            return {"error": "MCP runtime unavailable"}
        return await self._mcp_runtime.call_tool(tool_call)

    async def _execute_tool_call(
        self,
        tool_call: dict[str, Any],
        tool_name: str,
        tool_args: dict[str, Any],
        system_tool_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute either a system tool or an MCP tool call."""
        if system_tool_context:
            from ..tools.system_registry import execute_system_tool, get_system_tool

            tool_registry = system_tool_context.get("tool_registry")
            state_manager = system_tool_context.get("state_manager")
            state = system_tool_context.get("state")
            default_user_input = system_tool_context.get("default_user_input", "")
            tool_user_input = str(tool_args.get("request") or default_user_input or "")

            if (
                callable(get_system_tool)
                and callable(execute_system_tool)
                and get_system_tool(tool_name)
            ):
                data_out, err, state_updates, _retry = await execute_system_tool(
                    tool_name=tool_name,
                    tool_registry=tool_registry,
                    llm_client=self,
                    state=state,
                    state_manager=state_manager,
                    user_input=tool_user_input,
                    tool_args=tool_args,
                )

                if state_updates and isinstance(system_tool_context.get("state_updates"), dict):
                    system_tool_context["state_updates"].update(state_updates)
                if state_updates and isinstance(state, dict):
                    state.update(state_updates)

                if err:
                    return {"error": err}
                return {"result": data_out}

            # If it isn't a system tool, allow direct A2A agent invocation by name.
            if tool_registry is not None and hasattr(tool_registry, "find_by_name"):
                try:
                    agent_cfg = tool_registry.find_by_name(tool_name)
                except Exception:
                    agent_cfg = None

                agent_url = (
                    str(agent_cfg.get("url") or "").strip() if isinstance(agent_cfg, dict) else ""
                )
                agent_type = (
                    str(agent_cfg.get("type") or "").strip() if isinstance(agent_cfg, dict) else ""
                )
                agent_kind = (
                    str(agent_cfg.get("kind") or "").strip() if isinstance(agent_cfg, dict) else ""
                )
                agent_transport = (
                    str(agent_cfg.get("transport") or "http").strip()
                    if isinstance(agent_cfg, dict)
                    else ""
                )
                if (
                    isinstance(agent_cfg, dict)
                    and agent_url
                    and agent_transport == "http"
                    and agent_type != "mcp_tool"
                    and agent_kind != "mcp_tool"
                ):
                    forwarded_args = dict(tool_args)
                    forwarded_args.setdefault("agent", str(agent_cfg.get("name") or tool_name))

                    data_out, err, state_updates, _retry = await execute_system_tool(
                        tool_name="call_agent",
                        tool_registry=tool_registry,
                        llm_client=self,
                        state=state,
                        state_manager=state_manager,
                        user_input=tool_user_input,
                        tool_args=forwarded_args,
                    )
                    if state_updates and isinstance(system_tool_context.get("state_updates"), dict):
                        system_tool_context["state_updates"].update(state_updates)
                    if state_updates and isinstance(state, dict):
                        state.update(state_updates)
                    if err:
                        return {"error": err}
                    return {"result": data_out}

        # Default: call MCP tool
        return await self._call_mcp_tool({**tool_call, "arguments": tool_args})

    def _get_json_response_format(self, response_model: type, schema_name: str) -> dict[str, Any]:
        """Return a response_format payload for structured output."""
        return _build_json_schema_format(response_model, schema_name)

    @staticmethod
    def _to_azure_text_format(response_format: dict[str, Any]) -> dict[str, Any]:
        if response_format.get("type") != "json_schema":
            return response_format

        json_schema = response_format.get("json_schema")
        if not isinstance(json_schema, dict):
            return response_format

        return {
            "type": "json_schema",
            "name": json_schema.get("name") or "structured_output",
            "schema": json_schema.get("schema") or {},
            "strict": bool(json_schema.get("strict", True)),
        }

    def _extract_text_and_reasoning(self, data: dict[str, Any]) -> tuple[str, str]:
        """Extract response text and optional reasoning trace from /responses output."""
        text_parts: list[str] = []
        reasoning_parts: list[str] = []

        def _append_text_candidate(candidate: Any) -> None:
            if candidate is None:
                return
            if isinstance(candidate, str):
                if candidate.strip():
                    text_parts.append(candidate)
                return
            if isinstance(candidate, list):
                for item in candidate:
                    _append_text_candidate(item)
                return
            if isinstance(candidate, dict):
                for key in (
                    "text",
                    "value",
                    "content",
                    "output_text",
                    "refusal",
                    "message",
                    "parts",
                ):
                    if key in candidate:
                        _append_text_candidate(candidate.get(key))

        # Primary text response
        if output_text := data.get("output_text"):
            _append_text_candidate(output_text)

        # Some providers return final text in top-level `text`
        _append_text_candidate(data.get("text"))

        # Structured output items
        for item in data.get("output", []):
            item_type = item.get("type")

            if item_type == "message":
                content = item.get("content", "")
                if isinstance(content, (str, dict)):
                    _append_text_candidate(content)
                elif isinstance(content, list):
                    for part in content:
                        if not isinstance(part, dict):
                            continue
                        part_type = part.get("type")
                        if part_type in ("output_text", "text"):
                            _append_text_candidate(part.get("text"))
                            _append_text_candidate(part.get("value"))
                            _append_text_candidate(part.get("content"))
                        elif part_type == "refusal":
                            _append_text_candidate(part.get("refusal") or part.get("text"))
                        elif part_type in ("reasoning", "reasoning_text", "reasoning_summary"):
                            reasoning_parts.append(part.get("text", ""))

            elif item_type in ("output_text", "text"):
                _append_text_candidate(item.get("text"))
                _append_text_candidate(item.get("value"))
                _append_text_candidate(item.get("content"))

            elif item_type in ("reasoning", "reasoning_text", "reasoning_summary"):
                reasoning_parts.append(str(item.get("content") or item.get("text", "")))

            elif item_type == "refusal":
                _append_text_candidate(item.get("refusal") or item.get("text"))

        # Top-level reasoning
        if reasoning := data.get("reasoning"):
            reasoning_parts.append(str(reasoning))

        text = self._sanitize_user_visible_text("".join(text_parts).strip())
        reasoning = "".join(reasoning_parts).strip()

        if not text:
            error_payload = data.get("error")
            status = data.get("status")
            incomplete_details = data.get("incomplete_details")
            truncation = data.get("truncation")
            top_text = data.get("text")
            output_value = data.get("output")
            output_items: list[Any] = output_value if isinstance(output_value, list) else []

            output_summaries: list[dict[str, Any]] = []
            for item in output_items[:5]:
                if not isinstance(item, dict):
                    output_summaries.append({"type": type(item).__name__})
                    continue
                content = item.get("content")
                content_shape: Any
                if isinstance(content, list):
                    content_shape = [
                        {
                            "type": c.get("type") if isinstance(c, dict) else type(c).__name__,
                            "keys": list(c.keys())[:8] if isinstance(c, dict) else None,
                        }
                        for c in content[:5]
                    ]
                elif isinstance(content, dict):
                    content_shape = {"keys": list(content.keys())[:12]}
                else:
                    content_shape = type(content).__name__

                output_summaries.append(
                    {
                        "type": item.get("type"),
                        "role": item.get("role"),
                        "status": item.get("status"),
                        "keys": list(item.keys())[:12],
                        "content_shape": content_shape,
                    }
                )

            if error_payload:
                logger.warning(
                    "Empty LLM response with provider error - status=%s error=%s incomplete_details=%s truncation=%s keys=%s top_text=%r output_summaries=%s",
                    status,
                    error_payload,
                    incomplete_details,
                    truncation,
                    list(data.keys()),
                    top_text,
                    output_summaries,
                )
            else:
                logger.info(
                    "Empty LLM response without provider error - status=%s incomplete_details=%s truncation=%s keys=%s top_text=%r output_summaries=%s",
                    status,
                    incomplete_details,
                    truncation,
                    list(data.keys()),
                    top_text,
                    output_summaries,
                )

        return text, reasoning

    @staticmethod
    def _sanitize_user_visible_text(text: str) -> str:
        """Remove provider/tool markup artifacts from user-visible text."""
        if not text:
            return text

        # Some providers emit inline tool-call traces (e.g.
        # "tool_name{...json...}") as plain text. Strip these blocks so
        # user-facing responses remain clean.
        cleaned = INLINE_TOOL_MARKUP_PATTERN.sub("", text)

        # Normalize accidental leading blank lines introduced by stripping.
        return cleaned.lstrip()

    def _supports_temperature(self) -> bool:
        """Return True if temperature parameter is supported for the current model."""
        # Some models don't support temperature parameter
        unsupported_keywords = {"gpt-5.2-chat"}
        model_lower = self.model.lower()
        return not any(keyword in model_lower for keyword in unsupported_keywords)

    def _is_temperature_unsupported(self, response: httpx.Response | None) -> bool:
        """Detect unsupported temperature errors from provider responses."""
        if response is None:
            return False
        try:
            data = response.json()
            message = str(data.get("error", {}).get("message", ""))
        except Exception:
            message = response.text
        message_lower = message.lower()
        return "temperature" in message_lower and "unsupported" in message_lower

    async def _post_response(
        self,
        input_messages: list[dict[str, Any]],
        response_format: dict[str, Any] | None = None,
        include_reasoning: bool = False,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        max_tool_rounds: int = 4,
        system_tool_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Call /responses endpoint with optional tool calling loop."""
        merged_tools = self._resolve_request_tools(tools, response_format)

        payload: dict[str, Any] = {
            "model": self.model,
            "input": input_messages,
            "max_output_tokens": max_tokens,
        }

        if response_format:
            payload["text"] = {"format": self._to_azure_text_format(response_format)}
        if merged_tools:
            payload["tools"] = merged_tools
            # When requesting strict structured output, prevent tool calling while
            # still sending tool definitions upstream.
            if tool_choice is not None:
                payload["tool_choice"] = tool_choice
            elif response_format is not None:
                payload["tool_choice"] = "none"
            else:
                # Be explicit: some providers default tool calling off unless
                # tool_choice is set.
                payload["tool_choice"] = "auto"
        if include_reasoning:
            payload["reasoning"] = {"summary": "auto"}
        if self._supports_temperature():
            payload["temperature"] = temperature

        # Initial call
        try:
            response = await self.client.post(self.endpoint_url, json=payload)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as exc:
            # Retry without temperature if unsupported
            if self._is_temperature_unsupported(exc.response):
                logger.debug("Temperature unsupported, retrying without it")
                payload.pop("temperature", None)
                response = await self.client.post(self.endpoint_url, json=payload)
                response.raise_for_status()
                data = response.json()
            else:
                raise

        # Tool calling loop (MCP + system tools)
        tool_round = 0
        tool_calls = self._extract_tool_calls(data)
        seen_calls: set[str] = set()

        while tool_calls and tool_round < max_tool_rounds:
            logger.info("Executing %d tool call(s) - round %d", len(tool_calls), tool_round + 1)
            await self._push_event(
                event_type="mcp_tool_round_started",
                content=f"Executing {len(tool_calls)} tool call(s) (round {tool_round + 1})",
                metadata={"round": tool_round + 1, "count": len(tool_calls)},
            )

            tool_result_messages: list[dict[str, Any]] = []

            for tool_call in tool_calls:
                tool_call_id = (
                    tool_call.get("call_id") or tool_call.get("id") or tool_call.get("tool_call_id")
                )
                tool_name = (
                    tool_call.get("name")
                    or tool_call.get("tool_name")
                    or tool_call.get("function", {}).get("name")
                    or "(unknown)"
                )

                raw_args = (
                    tool_call.get("arguments")
                    or tool_call.get("input")
                    or tool_call.get("params")
                    or tool_call.get("function", {}).get("arguments")
                    or {}
                )
                if isinstance(raw_args, str):
                    try:
                        tool_args = json.loads(raw_args)
                    except json.JSONDecodeError:
                        tool_args = {"raw": raw_args}
                elif isinstance(raw_args, dict):
                    tool_args = raw_args
                else:
                    tool_args = {"raw": raw_args}

                # Safety: detect repeated identical tool calls.
                try:
                    sig = f"{tool_name}:{json.dumps(tool_args, sort_keys=True, default=str)}"
                except Exception:
                    sig = f"{tool_name}:{str(tool_args)}"

                await self._push_event(
                    event_type="mcp_tool_call_started",
                    content=f"Calling tool: {tool_name}",
                    metadata={
                        "tool": tool_name,
                        "tool_call_id": tool_call_id,
                        "type": tool_call.get("type"),
                    },
                )

                if sig in seen_calls:
                    result: dict[str, Any] = {"error": f"Repeated tool call detected: {tool_name}"}
                else:
                    seen_calls.add(sig)
                    result = await self._execute_tool_call(
                        tool_call=tool_call,
                        tool_name=tool_name,
                        tool_args=tool_args,
                        system_tool_context=system_tool_context,
                    )

                success = not (isinstance(result, dict) and result.get("error"))
                await self._push_event(
                    event_type="mcp_tool_call_finished",
                    content=f"Tool {tool_name} {'succeeded' if success else 'failed'}",
                    metadata={
                        "tool": tool_name,
                        "tool_call_id": tool_call_id,
                        "success": success,
                        "error": (result.get("error") if isinstance(result, dict) else None),
                    },
                )

                result_text = json.dumps(result, default=str)
                tool_result_messages.append(
                    {
                        "role": "user",
                        "content": f"[TOOL_RESULT tool={tool_name} id={tool_call_id}] {result_text}",
                    }
                )

            # Feed tool results back into the next round as plain-text input.
            input_messages.extend(tool_result_messages)
            payload["input"] = input_messages

            # Next round
            response = await self.client.post(self.endpoint_url, json=payload)
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                body_preview = (exc.response.text or "").strip()
                if len(body_preview) > 2000:
                    body_preview = body_preview[:2000] + "..."
                logger.error(
                    "Tool-loop follow-up /responses request failed (status=%s): %s",
                    exc.response.status_code,
                    body_preview,
                )
                await self._push_event(
                    event_type="mcp_tool_followup_failed",
                    content=f"Tool-loop follow-up request failed ({exc.response.status_code})",
                    metadata={
                        "status_code": exc.response.status_code,
                        "body": body_preview,
                    },
                )
                raise

            data = response.json()
            tool_calls = self._extract_tool_calls(data)
            tool_round += 1

            await self._push_event(
                event_type="mcp_tool_round_finished",
                content=f"Finished tool round {tool_round}",
                metadata={"round": tool_round, "remaining_calls": len(tool_calls)},
            )

        if tool_calls:
            logger.warning("Tool call loop reached max rounds (%d)", max_tool_rounds)
            await self._push_event(
                event_type="mcp_tool_round_limit_reached",
                content=f"Tool call loop reached max rounds ({max_tool_rounds})",
                metadata={"max_tool_rounds": max_tool_rounds, "remaining_calls": len(tool_calls)},
            )

        return cast(dict[str, Any], data)

    async def chat(
        self,
        prompt: str | list[dict[str, str]],
        system_prompt: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        response_format: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
        max_tool_rounds: int = 4,
        system_tool_context: dict[str, Any] | None = None,
    ) -> str:
        """Send a prompt or message history to /responses and return text.

        ``prompt`` may be a single user prompt string or a full conversation
        history as a list of ``{"role", "content"}`` messages.
        """
        if isinstance(prompt, str):
            messages: list[dict[str, str]] = [{"role": "user", "content": prompt}]
        else:
            messages = prompt

        if response_format:
            instruction = "Return only valid JSON that matches the provided schema. Do not include any extra text."
            system_prompt = f"{system_prompt}\n\n{instruction}" if system_prompt else instruction
        input_messages = self._build_input_messages(messages, system_prompt)

        data = await self._post_response(
            input_messages=input_messages,
            response_format=response_format,
            include_reasoning=False,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tools,
            max_tool_rounds=max_tool_rounds,
            system_tool_context=system_tool_context,
        )
        text, _reasoning = self._extract_text_and_reasoning(data)
        return text

    async def chat_structured(
        self,
        prompt: str,
        response_model: type[TModel],
        schema_name: str,
        system_prompt: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> TModel:
        """Send a single-prompt request and return a validated structured model."""
        response_format = self._get_json_response_format(response_model, schema_name)
        response = await self.chat(
            prompt=prompt,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
        )

        raw_data = json.loads(response)
        return response_model.model_validate(raw_data)
