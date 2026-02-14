"""
Decide Node - Routes user input to appropriate action path.

Fast path: keyword match on registered tools -> execute tool -> LLM responds.
Slow path: LLM decides (with full tool context) -> route to system_tool/a2a/mcp/build -> execute -> LLM responds.
"""

import inspect
import json
import logging
import re
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any, cast

from openai import APIConnectionError, RateLimitError
from pydantic import BaseModel, Field

from ..env_utils import env_bool, env_float, env_int
from ..llm.conversation_compressor import ConversationCompressor
from ..llm.prompts import PromptTemplates
from ..llm.response_models import DecisionResponse, validate_decision_response
from ..nodes.node_helpers.formatters import format_tool_result
from ..nodes.node_helpers.registry import execute_system_tool, match_tool_by_input
from ..runtime.time_utils import utcnow_isoformat
from ..state.helpers import is_work_item_actionable
from ..state.models import (
    ConversationMessage,
    Decision,
    DecisionAction,
    ProgenitorState,
)
from ..tools.mcp import A2AClient
from ..tools.memory import format_memory_context, get_relevant_memories

if TYPE_CHECKING:
    from ..state.execution_schemas import BuildSettings

logger = logging.getLogger(__name__)

_prompts = PromptTemplates()

try:
    _GENESIS_PROMPT = _prompts.get_genesis_prompt().strip()
except Exception:
    _GENESIS_PROMPT = ""


def _is_genesis_message(role: str, content: str) -> bool:
    """Return True when a message is the genesis bootstrap prompt."""
    return role == "system" and bool(_GENESIS_PROMPT) and content.strip() == _GENESIS_PROMPT


def _compose_user_response_system_prompt(extra_prompt: str | None = None) -> str | None:
    """Compose system prompt for user-facing responses with genesis first."""
    base = _GENESIS_PROMPT
    extra = (extra_prompt or "").strip()

    if base and extra:
        return f"{base}\n\n{extra}"
    if base:
        return base
    return extra or None

# ---------------------------------------------------------------------------
# Build-related helpers (kept here as they are decide-node concerns, not tools)
# ---------------------------------------------------------------------------

class BuildNameExtraction(BaseModel):
    """Structured output for extracting build/agent name from user request."""

    project_name: str = Field(
        description="The extracted project/agent name in snake_case format. "
        "Should be a concise, descriptive name based on what the user wants to build. "
        "Examples: 'weather_agent', 'stock_tracker', 'email_assistant'. "
        "Do NOT include filler words like 'for_me', 'please', 'a', 'the'."
    )
    build_type: str = Field(
        description="The type of build: 'agent' for A2A agents, 'mcp' for MCP servers/tools."
    )
    reasoning: str = Field(
        description="Brief explanation of how the name was derived from the user request."
    )


def _infer_target_capabilities(user_input: str) -> list[str]:
    """Infer a likely capability name from the user's request."""
    text = (user_input or "").lower()

    if "hello world" in text or "hello-world" in text:
        return ["hello_world_a2a_agent"]

    match = re.search(
        r"\b(?:build|create|scaffold|generate|make|implement)\s+(?:an?\s+)?([a-z0-9 _\-]{1,64})\s+agent\b",
        text,
    )
    if not match:
        return []

    raw_name = match.group(1).strip()
    slug = re.sub(r"[^a-z0-9]+", "_", raw_name).strip("_")
    if not slug:
        return []

    return [f"{slug}_a2a_agent"]


def _clean_project_name(name: str) -> str:
    """Clean and normalize a project name to snake_case."""
    if not name:
        return ""

    cleaned = re.sub(r'[^a-z0-9_]', '_', name.lower()).strip('_')

    filler_patterns = [
        r'^for_me_', r'^please_', r'^a_', r'^an_', r'^the_', r'^new_',
        r'^build_', r'^create_', r'_for_me$', r'_please$',
    ]
    for pattern in filler_patterns:
        cleaned = re.sub(pattern, '', cleaned)

    cleaned = re.sub(r'_+', '_', cleaned).strip('_')
    return cleaned


async def _request_decision_data(
    llm_client,
    user_input: str,
    conversation_history: list[dict[str, str]],
    tool_context: str,
) -> dict[str, Any]:
    """Request routing decision from LLM using structured output."""
    try:
        inspect.getattr_static(llm_client, "chat_structured")
        has_real_chat_structured = True
    except AttributeError:
        has_real_chat_structured = False

    fallback_decide = getattr(llm_client, "decide", None)
    if not has_real_chat_structured and callable(fallback_decide):
        fallback_result = fallback_decide(
            user_input=user_input,
            conversation_history=conversation_history,
            tool_context=tool_context,
        )
        if inspect.isawaitable(fallback_result):
            decision = await fallback_result
        else:
            decision = fallback_result
        if not isinstance(decision, dict):
            return {}
        logger.info(
            "DECIDE TEST: action=%s confidence=%s targets=%s",
            decision.get("action"),
            decision.get("confidence"),
            decision.get("target_capabilities"),
        )
        return decision

    system_prompt = _prompts.get_decide_prompt(tool_context)
    messages = conversation_history[-10:] + [{"role": "user", "content": user_input}]

    try:
        if hasattr(llm_client, "chat_structured"):
            parsed = await llm_client.chat_structured(
                messages=messages,
                response_model=DecisionResponse,
                schema_name="decision_response",
                system_prompt=system_prompt,
                temperature=0.3,
                tools=[],
            )
            validated = validate_decision_response(parsed.model_dump())
            decision = validated.model_dump()
            logger.info(
                "DECIDE TEST: action=%s confidence=%s targets=%s",
                decision.get("action"),
                decision.get("confidence"),
                decision.get("target_capabilities"),
            )
            return decision

        response_format = llm_client._get_json_response_format(DecisionResponse, "decision_response")
        response = await llm_client.chat(
            messages=messages,
            system_prompt=system_prompt,
            temperature=0.3,
            response_format=response_format,
            tools=[],
        )
        parsed = json.loads(response)
        validated = validate_decision_response(parsed)
        decision = validated.model_dump()
        logger.info(
            "DECIDE TEST: action=%s confidence=%s targets=%s",
            decision.get("action"),
            decision.get("confidence"),
            decision.get("target_capabilities"),
        )
        return decision
    except Exception as exc:
        logger.warning("Failed to get structured decide response: %s", exc)
        validated = validate_decision_response({})
        return validated.model_dump()


async def _generate_build_settings(
    user_input: str,
    llm_client,
    llm_action: str,
    decision_data: dict | None = None,
) -> "BuildSettings":
    """Generate structured BuildSettings, using decision data if available."""
    import uuid

    from ..state.execution_schemas import BuildSettings, BuildType

    build_id = f"build_{uuid.uuid4().hex[:12]}"

    project_name = None
    build_type = BuildType.AGENT
    build_description = None

    # Primary: Use values from the decision response if available
    if decision_data:
        decision_project_name = decision_data.get("project_name")
        decision_build_type = decision_data.get("build_type")
        decision_build_description = decision_data.get("build_description")

        if decision_project_name:
            project_name = _clean_project_name(decision_project_name)
            if project_name:
                logger.info("Using project_name from decision response: %s", project_name)

        if decision_build_type:
            if decision_build_type.lower() == "mcp":
                build_type = BuildType.MCP
            logger.info("Using build_type from decision response: %s", build_type)

        if decision_build_description:
            build_description = decision_build_description

    # Fallback: Use LLM structured output if decision didn't provide project_name
    if not project_name and llm_client:
        try:
            system_prompt = _prompts.get("decide.project_name_system.md")

            prompt = _prompts.get(
                "decide.project_name.md",
                user_input=user_input,
            )

            if hasattr(llm_client, "chat_structured"):
                parsed_model = await llm_client.chat_structured(
                    messages=[{"role": "user", "content": prompt}],
                    response_model=BuildNameExtraction,
                    schema_name="build_name_extraction",
                    system_prompt=system_prompt,
                    temperature=0.1,
                )
                extracted_name = _clean_project_name(parsed_model.project_name)
                extracted_type = parsed_model.build_type.strip().lower()
            else:
                response_format = llm_client._get_json_response_format(
                    BuildNameExtraction, "build_name_extraction"
                )

                actual_prompt = prompt
                if response_format.get("type") == "json_object":
                    actual_prompt = prompt + "\n\nRespond with valid JSON matching this schema: {project_name: string, build_type: 'agent'|'mcp', reasoning: string}"

                response = await llm_client.complete(
                    prompt=actual_prompt,
                    system_prompt=system_prompt,
                    temperature=0.1,
                    response_format=response_format,
                )
                parsed = json.loads(response.strip())
                extracted_name = _clean_project_name(parsed.get("project_name", ""))
                extracted_type = parsed.get("build_type", "agent").strip().lower()

            if extracted_name and len(extracted_name) > 1:
                project_name = extracted_name
                logger.info("LLM extracted project name: %s", project_name)

            if extracted_type == "mcp":
                build_type = BuildType.MCP

        except Exception as e:
            logger.warning("Failed to extract project name via LLM structured output: %s", e)

    # Fallback: Try regex patterns if LLM failed
    if not project_name:
        text = user_input.lower()
        name_patterns = [
            r'\b(?:build|create|scaffold|generate|make|implement)\s+(?:for\s+me\s+)?(?:me\s+)?(?:please\s+)?(?:a\s+|an\s+)?(?:new\s+)?(?:simple\s+|basic\s+)?([a-z][a-z0-9]*(?:\s+[a-z][a-z0-9]*)?)\s+(?:a2a\s+)?(?:agent|mcp)',
            r'\bagent\s+(?:named?|called?)\s+([a-z0-9_\-]+)',
            r'\b(?:named?|called?)\s+([a-z0-9_\-]+)',
        ]

        for pattern in name_patterns:
            match = re.search(pattern, text)
            if match:
                raw_name = match.group(1).strip()
                skip_words = {"a", "an", "the", "new", "simple", "basic", "for", "me", "please"}
                if raw_name and raw_name not in skip_words:
                    project_name = re.sub(r'\s+', '_', raw_name)
                    logger.info("Regex extracted project name: %s", project_name)
                    break

    # Determine build type from text if not already set
    text = user_input.lower()
    if build_type == BuildType.AGENT and ("mcp" in text or "mcp server" in text or "mcp tool" in text):
        build_type = BuildType.MCP

    # Final fallback: extract meaningful words
    if not project_name:
        exclude_words = {
            'build', 'create', 'make', 'scaffold', 'generate', 'implement',
            'an', 'a', 'the', 'me', 'for', 'please', 'new', 'simple', 'basic',
            'agent', 'mcp', 'server', 'tool', 'want', 'need', 'would', 'like',
            'can', 'you', 'i', 'that', 'which', 'to', 'with'
        }
        words = [w for w in re.findall(r'[a-z]+', text) if w not in exclude_words and len(w) > 2]
        if words:
            project_name = '_'.join(words[:2])
        else:
            import uuid as _uuid
            project_name = f"project_{_uuid.uuid4().hex[:6]}"

    description = build_description or f"Build {build_type.value}: {project_name}"

    return BuildSettings.create_from_llm_output(
        build_id=build_id,
        project_name=project_name,
        build_type=build_type,
        user_request=user_input,
        description=description,
    )


# ---------------------------------------------------------------------------
# Shared helpers used by both fast and slow paths
# ---------------------------------------------------------------------------

def _extract_history(
    messages: list,
    limit: int = 10,
    memory_context: str | None = None,
    exclude_latest_user_input: str | None = None,
) -> list[dict[str, str]]:
    """Extract conversation history as list of dicts."""
    history = []
    if memory_context:
        history.append({"role": "system", "content": memory_context})
    allowed_roles = {"system", "user", "assistant", "tool"}
    recent_messages = messages[-limit:]
    last_index = len(recent_messages) - 1
    for idx, msg in enumerate(recent_messages):
        if isinstance(msg, dict):
            raw_role = msg.get("role", "user")
            role = raw_role if raw_role in allowed_roles else "user"
            content = msg.get("content", "")
            if _is_genesis_message(role, content):
                continue
            if (
                exclude_latest_user_input is not None
                and idx == last_index
                and role == "user"
                and content == exclude_latest_user_input
            ):
                continue
            history.append({"role": role, "content": content})
        elif hasattr(msg, "role"):
            raw_role = msg.role
            role = raw_role if raw_role in allowed_roles else "user"
            content = msg.content
            if _is_genesis_message(role, content):
                continue
            if (
                exclude_latest_user_input is not None
                and idx == last_index
                and role == "user"
                and content == exclude_latest_user_input
            ):
                continue
            history.append({"role": role, "content": content})
    return history


async def _respond_with_context(
    user_input: str,
    context_label: str,
    context_data: str,
    state: "ProgenitorState",
    llm_client,
    memory_context: str | None = None,
) -> tuple[str, bool]:
    """Generate LLM response incorporating tool/agent call results.

    Returns:
        Tuple of (response_text, was_streamed).
    """
    from ..runtime.loop import get_current_loop
    loop = get_current_loop()

    history = _extract_history(
        state.get("messages", []),
        limit=10,
        memory_context=memory_context,
        exclude_latest_user_input=user_input,
    )

    system_prompt = _compose_user_response_system_prompt(_prompts.get(
        "decide.tool_response_system.md",
        context_label=context_label,
        context_data=context_data,
    ))

    messages = history + [{"role": "user", "content": user_input}]

    can_stream = loop is not None and getattr(loop, '_streaming_enabled', False)

    if can_stream:
        assert loop is not None
        streamed = ""
        async for token in llm_client.chat_stream(
            messages=messages,
            system_prompt=system_prompt,
            temperature=0.7,
            tools=[],
        ):
            streamed += token
            await loop.stream_token(token)
        if streamed.strip():
            loop._streamed_response_content = streamed
            loop._response_was_streamed = True
            return streamed, True

        logger.warning(
            "Streaming returned empty content for context_label=%s; falling back to non-stream response",
            context_label,
        )
        try:
            fallback = await llm_client.chat(
                messages=messages,
                system_prompt=system_prompt,
                temperature=0.7,
                tools=[],
            )
            if isinstance(fallback, str) and fallback.strip():
                loop._streamed_response_content = ""
                loop._response_was_streamed = False
                return fallback, False
        except Exception as fallback_exc:
            logger.warning("Non-stream fallback failed for context_label=%s: %s", context_label, fallback_exc)

        loop._streamed_response_content = ""
        loop._response_was_streamed = False
        return context_data, False
    else:
        response = await llm_client.chat(
            messages=messages,
            system_prompt=system_prompt,
            temperature=0.7,
            tools=[],
        )
        return response, False


async def _push_activity_event(
    event_type: str,
    content: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Push a lightweight activity event to connected clients, if available."""
    try:
        from ..runtime.loop import get_current_loop
        loop = get_current_loop()
        if loop and hasattr(loop, "_push_event"):
            await loop._push_event(event_type, content, metadata)
    except Exception as exc:
        logger.debug("Failed to push activity event %s: %s", event_type, exc)


async def _execute_and_respond(
    tool_name: str,
    user_input: str,
    state: "ProgenitorState",
    llm_client,
    tool_registry,
    state_manager,
    memory_context: str | None = None,
    execution_source: str = "unspecified",
    progress_message: str | None = None,
) -> tuple["ProgenitorState | None", bool]:
    """Execute a system tool via the registry and generate an LLM-enriched response.

    This is the unified fast/slow path for system tools:
    1. Execute tool via node_helpers.execute_system_tool (dynamic dispatch)
    2. Format result via node_helpers.format_tool_result (dynamic dispatch)
    3. Feed result to LLM for a natural response to the user
    4. Merge any state_updates from the tool into the returned state

    Returns:
        Tuple of (state, should_retry_slow_path).
        If should_retry_slow_path is True, the fast-path failed and should fall back
        to the slow path. In this case, state will be None.
    """
    await _push_activity_event(
        event_type="activity_started",
        content=((progress_message or "").strip() or f"Running tool: {tool_name}"),
        metadata={
            "activity": "system_tool",
            "tool_name": tool_name,
            "source": execution_source,
        },
    )

    result, error, state_updates, should_retry_slow_path = await execute_system_tool(
        tool_name, tool_registry, llm_client, state, state_manager,
        user_input=user_input,
    )

    # If the tool explicitly requests slow path retry, return early
    if should_retry_slow_path:
        logger.info("Tool %s requested slow path fallback: %s", tool_name, error or "unknown reason")
        return None, True

    if error or result is None:
        context_data = f"Error: {error or 'No result'}"
    else:
        context_data = format_tool_result(tool_name, result)

    try:
        response_text, was_streamed = await _respond_with_context(
            user_input, tool_name, context_data, state, llm_client, memory_context
        )
        llm_enriched = True
    except Exception as e:
        logger.warning("LLM enrichment failed for %s, using raw result: %s", tool_name, e)
        response_text = context_data
        was_streamed = False
        llm_enriched = False

    decision = Decision(
        action=DecisionAction.ANSWER_DIRECTLY,
        reasoning=f"System tool: {tool_name} with LLM-enriched response",
        requires_planning=False,
        direct_response=None if was_streamed else response_text,
    )

    if state_manager:
        await state_manager.log_decision({
            "event": "system_tool_executed",
            "tool_name": tool_name,
            "llm_enriched": llm_enriched,
            "timestamp": utcnow_isoformat(),
        })

    result_state: dict[str, Any] = {"current_decision": decision}

    # Merge any state_updates from the tool (e.g. schedule entries)
    if state_updates:
        result_state.update(state_updates)

    return cast(ProgenitorState, result_state), False


def _format_a2a_response(result: dict, agent_name: str) -> str:
    """Format an A2A agent response for display."""
    if result.get("success", False):
        response = result.get("response") or result.get("result") or result.get("data")
        if isinstance(response, dict):
            return str(response)
        return str(response) if response else "Agent completed successfully."
    else:
        error = result.get("error", "Unknown error")
        return f"Agent '{agent_name}' error: {error}"


# ---------------------------------------------------------------------------
# Default confidence threshold (used when config not provided)
# ---------------------------------------------------------------------------
DEFAULT_BUILD_CONFIDENCE_THRESHOLD = 0.95


def create_node(
    llm_client,
    tool_registry,
    state_manager=None,
    config=None,
) -> Callable[[ProgenitorState], Coroutine[Any, Any, ProgenitorState]]:
    """Create the decide node function.

    Args:
        llm_client: The LLM client for making decisions
        tool_registry: Registry of available tools and agents
        state_manager: Optional state manager for logging decisions
        config: Optional ProgenitorConfig for runtime settings
    """
    build_confidence_threshold = DEFAULT_BUILD_CONFIDENCE_THRESHOLD
    if config and hasattr(config, 'runtime'):
        build_confidence_threshold = getattr(
            config.runtime, 'build_confidence_threshold', DEFAULT_BUILD_CONFIDENCE_THRESHOLD
        )

    async def decide_node(state: ProgenitorState) -> ProgenitorState:
        """Make a routing decision for the current input."""
        user_input = state.get("current_input")

        if not user_input:
            backlog = state.get("backlog", [])
            pending = [
                item for item in backlog
                if is_work_item_actionable(item)
            ]

            if pending:
                return {"current_decision": None}

            return {"current_decision": None}

        auto_updates: dict[str, Any] = {}

        def _merge_updates(payload: dict[str, Any] | ProgenitorState) -> ProgenitorState:
            if auto_updates:
                payload = {**payload, **auto_updates}
            return cast(ProgenitorState, payload)

        def _coerce_messages(raw_messages: list[Any]) -> list[ConversationMessage]:
            messages: list[ConversationMessage] = []
            for msg in raw_messages:
                if isinstance(msg, ConversationMessage):
                    if _is_genesis_message(msg.role, msg.content):
                        continue
                    messages.append(msg)
                elif isinstance(msg, dict):
                    try:
                        parsed = ConversationMessage(**msg)
                        if _is_genesis_message(parsed.role, parsed.content):
                            continue
                        messages.append(parsed)
                    except Exception:
                        continue
            return messages

        if env_bool("PROGENITOR_AUTO_COMPRESS_ENABLED", True):
            threshold = env_int("PROGENITOR_AUTO_COMPRESS_THRESHOLD", 100)
            keep_recent = env_int("PROGENITOR_AUTO_COMPRESS_KEEP_RECENT", 50)
            max_summary_tokens = env_int("PROGENITOR_AUTO_COMPRESS_MAX_SUMMARY_TOKENS", 500)
            persist = env_bool("PROGENITOR_AUTO_COMPRESS_PERSIST", True)

            raw_messages = state.get("messages", [])
            messages_for_compress = _coerce_messages(raw_messages)

            if len(messages_for_compress) >= threshold and len(messages_for_compress) > keep_recent:
                compressor = ConversationCompressor(
                    llm_client=llm_client,
                    max_summary_tokens=max_summary_tokens,
                )
                try:
                    compressed = await compressor.compress(messages_for_compress, keep_recent=keep_recent)
                    auto_updates["messages"] = compressed
                    state["messages"] = compressed

                    if persist and state_manager:
                        await state_manager.save_messages(compressed)

                    logger.info(
                        "Auto-compressed conversation: %d -> %d messages",
                        len(messages_for_compress), len(compressed),
                    )
                except Exception as e:
                    logger.warning("Auto compression failed: %s", e)

        memory_context = ""
        if env_bool("PROGENITOR_MEMORY_INJECT_ENABLED", True):
            mem_limit = env_int("PROGENITOR_MEMORY_INJECT_LIMIT", 5)
            mem_min_conf = env_float("PROGENITOR_MEMORY_INJECT_MIN_CONFIDENCE", 0.5)
            mem_max_chars = env_int("PROGENITOR_MEMORY_INJECT_MAX_CHARS", 1200)

            memories = get_relevant_memories(
                user_input,
                limit=mem_limit,
                min_confidence=mem_min_conf,
            )
            memory_context = format_memory_context(memories, max_chars=mem_max_chars)

        # ============================================================
        # FAST-PATH: Dynamic keyword/pattern match on registered tools
        # ============================================================
        matched_tool = match_tool_by_input(user_input)
        if matched_tool:
            logger.info("Fast-path match: tool=%s for input=%r", matched_tool, user_input[:80])
            result, should_retry_slow = await _execute_and_respond(
                matched_tool,
                user_input,
                state,
                llm_client,
                tool_registry,
                state_manager,
                memory_context,
                execution_source="fast_path",
            )
            if should_retry_slow:
                logger.info("Fast-path failed, falling back to slow path")
                # Continue to slow path below instead of returning
            elif result is not None:
                return _merge_updates(result)
            else:
                # This shouldn't happen, but handle gracefully
                logger.warning("Fast-path returned None result without retry flag")

        # ============================================================
        # SLOW PATH: Use LLM for routing decision
        # Tool context fills the context window so the LLM knows
        # available tools/agents to call
        # ============================================================

        # Build rich tool context for the LLM
        tool_context = tool_registry.get_tool_context()

        # Get conversation history for context
        messages = state.get("messages", [])
        history = _extract_history(
            messages,
            limit=10,
            memory_context=memory_context,
            exclude_latest_user_input=user_input,
        )

        try:
            # Ask LLM for decision (structured output with confidence)
            decision_data = await _request_decision_data(
                llm_client=llm_client,
                user_input=user_input,
                conversation_history=history,
                tool_context=tool_context,
            )

            action_str = decision_data.get("action", "answer_directly")
            confidence = decision_data.get("confidence", 0.5)
            user_response = decision_data.get("user_response") or decision_data.get("direct_response")
            progress_message = user_response

            llm_action = str(action_str).lower().strip()
            is_build_action = llm_action in ("build_agent", "build_tool", "upgrade")

            # --- Handle build actions ---
            if is_build_action:
                result = await _handle_build_action(
                    user_input, state, llm_client, tool_registry, state_manager,
                    decision_data, llm_action, confidence, build_confidence_threshold,
                    user_response,
                )
                return _merge_updates(result)

            # Parse decision action
            try:
                action = DecisionAction(action_str)
            except ValueError:
                logger.warning("Unknown action '%s', falling back to answer_directly", action_str)
                action = DecisionAction.ANSWER_DIRECTLY

            # --- Handle system_tool action from LLM ---
            if action == DecisionAction.SYSTEM_TOOL:
                target_capabilities = decision_data.get("target_capabilities", [])
                tool_name = target_capabilities[0] if target_capabilities else None

                if tool_name:
                    logger.info("Slow-path: LLM selected system_tool=%s", tool_name)
                    result, should_retry_slow = await _execute_and_respond(
                        tool_name,
                        user_input,
                        state,
                        llm_client,
                        tool_registry,
                        state_manager,
                        memory_context,
                        execution_source="slow_path",
                        progress_message=progress_message,
                    )
                    # If slow path also requests retry, we can't retry again - just use the result
                    if not should_retry_slow and result:
                        return _merge_updates(result)
                    elif should_retry_slow:
                        logger.warning("Slow-path system_tool also failed - cannot retry further")
                        # Fall through to answer_directly below with error handling
                else:
                    logger.warning("system_tool action without target_capabilities")

            # --- Handle delegate_a2a ---
            if action == DecisionAction.DELEGATE_A2A:
                result = await _handle_a2a(
                    user_input, state, llm_client, tool_registry, state_manager,
                    decision_data, memory_context, progress_message,
                )
                return _merge_updates(result)

            # --- Handle use_mcp ---
            if action == DecisionAction.USE_MCP:
                result = await _handle_mcp(
                    user_input, state, llm_client, tool_registry, state_manager,
                    decision_data, memory_context, progress_message,
                )
                return _merge_updates(result)

            # --- Handle answer_directly (with streaming if enabled) ---
            direct_resp = None

            from ..runtime.loop import get_current_loop
            loop = get_current_loop()

            if action == DecisionAction.ANSWER_DIRECTLY and loop and loop._streaming_enabled:
                logger.info("STREAMING: Starting (loop id=%s)", id(loop))
                msgs = _extract_history(
                    state.get("messages", []),
                    limit=10,
                    memory_context=memory_context,
                    exclude_latest_user_input=user_input,
                )
                msgs.append({"role": "user", "content": user_input})

                streamed_text = ""
                chunk_count = 0
                try:
                    async for token in llm_client.chat_stream(
                        messages=msgs,
                        system_prompt=_compose_user_response_system_prompt(),
                        temperature=0.7,
                    ):
                        chunk_count += 1
                        streamed_text += token
                        await loop.stream_token(token)
                    estimated_tokens = max(1, len(streamed_text) // 4) if streamed_text else 0
                    logger.info(
                        "STREAMING: Completed - chunks=%d, estimated_tokens=%d, chars=%d",
                        chunk_count,
                        estimated_tokens,
                        len(streamed_text),
                    )
                except Exception as e:
                    estimated_tokens = max(1, len(streamed_text) // 4) if streamed_text else 0
                    logger.error(
                        "STREAMING: Error after chunks=%d, estimated_tokens=%d: %s",
                        chunk_count,
                        estimated_tokens,
                        e,
                    )
                    raise

                loop._streamed_response_content = streamed_text
                if streamed_text.strip():
                    direct_resp = None
                    loop._response_was_streamed = True
                else:
                    logger.warning("Direct-response streaming returned empty content; falling back to non-stream response")
                    loop._streamed_response_content = ""
                    loop._response_was_streamed = False
                    direct_resp = user_response or decision_data.get("direct_response")
                    try:
                        generated = await llm_client.chat(
                            messages=msgs,
                            system_prompt=_compose_user_response_system_prompt(),
                            temperature=0.7,
                        )
                        if isinstance(generated, str) and generated.strip():
                            direct_resp = generated
                    except Exception as e:
                        logger.warning("Failed non-stream fallback for direct response: %s", e)
                    if not direct_resp:
                        direct_resp = "I’ve processed your request."
            elif action == DecisionAction.ANSWER_DIRECTLY:
                direct_resp = user_response or decision_data.get("direct_response")
                msgs = _extract_history(
                    state.get("messages", []),
                    limit=10,
                    memory_context=memory_context,
                    exclude_latest_user_input=user_input,
                )
                msgs.append({"role": "user", "content": user_input})
                try:
                    generated = await llm_client.chat(
                        messages=msgs,
                        system_prompt=_compose_user_response_system_prompt(),
                        temperature=0.7,
                    )
                    if isinstance(generated, str) and generated.strip():
                        direct_resp = generated
                except Exception as e:
                    logger.warning("Failed to generate direct response with genesis prompt: %s", e)

            decision = Decision(
                action=action,
                reasoning=decision_data.get("reasoning", ""),
                requires_planning=decision_data.get("requires_planning", False),
                target_capabilities=decision_data.get("target_capabilities", []),
                direct_response=direct_resp,
            )

            decision_result: dict[str, Any] = {"current_decision": decision}

            if state_manager:
                await state_manager.log_decision({
                    "event": "routing_decision",
                    "action": action.value,
                    "confidence": confidence,
                    "reasoning": decision_data.get("reasoning", ""),
                    "requires_planning": decision_data.get("requires_planning", False),
                    "target_capabilities": decision_data.get("target_capabilities", []),
                    "timestamp": utcnow_isoformat(),
                })

            return _merge_updates(decision_result)

        except (RateLimitError, APIConnectionError) as e:
            logger.warning("Recoverable LLM error in decide node: %s", e)

            error_message = (
                "⚠️ Temporary Issue\n\n"
                f"I encountered an issue connecting to the AI service: {type(e).__name__}\n\n"
                "This is usually temporary. Please try again in a moment."
            )

            decision = Decision(
                action=DecisionAction.ANSWER_DIRECTLY,
                reasoning=f"LLM API error: {e}",
                requires_planning=False,
                direct_response=error_message,
            )

            if state_manager:
                await state_manager.log_decision({
                    "event": "routing_decision_error",
                    "error_type": type(e).__name__,
                    "error_message": str(e),
                    "recoverable": True,
                    "timestamp": utcnow_isoformat(),
                })

            return _merge_updates({"current_decision": decision})

        except Exception as e:
            logger.error("Unexpected error in decide node: %s", e, exc_info=True)

            error_message = (
                "❌ Error Processing Request\n\n"
                f"I encountered an unexpected error while processing your request:\n"
                f"{type(e).__name__}: {str(e)[:200]}\n\n"
                "This has been logged. You can try rephrasing your request or ask for help."
            )

            decision = Decision(
                action=DecisionAction.ANSWER_DIRECTLY,
                reasoning=f"Fallback due to error: {type(e).__name__}: {e}",
                requires_planning=False,
                direct_response=error_message,
            )

            if state_manager:
                await state_manager.log_decision({
                    "event": "routing_decision_error",
                    "error_type": type(e).__name__,
                    "error_message": str(e),
                    "recoverable": False,
                    "timestamp": utcnow_isoformat(),
                })

            return _merge_updates({"current_decision": decision})

    return decide_node


# ---------------------------------------------------------------------------
# Slow-path action handlers (extracted for readability)
# ---------------------------------------------------------------------------

async def _handle_build_action(
    user_input: str,
    state: "ProgenitorState",
    llm_client,
    tool_registry,
    state_manager,
    decision_data: dict,
    llm_action: str,
    confidence: float,
    build_confidence_threshold: float,
    user_response: str | None,
) -> "ProgenitorState":
    """Handle build_agent / build_tool / upgrade actions from LLM."""

    logger.info(
        "Build action requested: action=%s, confidence=%.2f, threshold=%.2f",
        llm_action, confidence, build_confidence_threshold,
    )

    if confidence < build_confidence_threshold:
        logger.info(
            "Build action rejected due to low confidence (%.2f < %.2f)",
            confidence, build_confidence_threshold,
        )
        decision = Decision(
            action=DecisionAction.ANSWER_DIRECTLY,
            reasoning=f"Build action confidence ({confidence:.2f}) below threshold ({build_confidence_threshold})",
            requires_planning=False,
            direct_response=(
                f"I'm not certain you want me to build an agent. "
                f"(Confidence: {confidence:.0%})\n\n"
                f"If you'd like me to build a new agent, please say something like:\n"
                f"- \"Build me a hello world agent\"\n"
                f"- \"Create a new agent that [does X]\"\n\n"
                f"Otherwise, let me know what you'd like me to do and I'll help!"
            ),
        )

        if state_manager:
            await state_manager.log_decision({
                "event": "build_rejected_low_confidence",
                "confidence": confidence,
                "threshold": build_confidence_threshold,
                "llm_action": llm_action,
                "timestamp": utcnow_isoformat(),
            })

        return {"current_decision": decision}

    # Check if a build is already in progress
    if state.get("build_in_progress", False):
        current_task = state.get("build_task_description", "unknown task")
        phase = state.get("build_phase", "unknown")
        decision = Decision(
            action=DecisionAction.ANSWER_DIRECTLY,
            reasoning="Build already in progress - rejecting new build request.",
            requires_planning=False,
            direct_response=(
                f"⚠️ Build Already In Progress\n\n"
                f"I'm currently working on: {current_task}\n"
                f"Current phase: {phase}\n\n"
                f"Please wait for the current build to complete, or ask me for build status to check progress."
            ),
        )

        if state_manager:
            await state_manager.log_decision({
                "event": "build_rejected",
                "reason": "build_in_progress",
                "current_task": current_task,
                "timestamp": utcnow_isoformat(),
            })

        return {"current_decision": decision}

    # Generate structured BuildSettings
    build_settings = await _generate_build_settings(
        user_input=user_input,
        llm_client=llm_client,
        llm_action=llm_action,
        decision_data=decision_data,
    )
    logger.info(
        "BUILD_SETTINGS: project=%s, type=%s, dir=%s",
        build_settings.project_name,
        build_settings.build_type,
        build_settings.project_directory,
    )

    # Start async build
    from ..runtime.background_build import get_background_runner

    runner = get_background_runner()
    task_description = f"Build {build_settings.build_type}: {build_settings.project_name}"
    target_capabilities = _infer_target_capabilities(user_input)

    if runner:
        try:
            build_id, ack_message = await runner.start_build(
                user_input=user_input,
                task_description=task_description,
                target_capabilities=target_capabilities,
                initial_state=dict(state),
                build_settings=build_settings,
            )

            logger.info("Started async build %s for: %s (confidence=%.2f)", build_id, task_description, confidence)

            response_msg = user_response or ack_message

            decision = Decision(
                action=DecisionAction.ANSWER_DIRECTLY,
                reasoning=f"Started async build {build_id} (confidence={confidence:.2f})",
                requires_planning=False,
                direct_response=response_msg,
            )

            if state_manager:
                await state_manager.log_decision({
                    "event": "async_build_started",
                    "build_id": build_id,
                    "task_description": task_description,
                    "build_settings": build_settings.model_dump(),
                    "confidence": confidence,
                    "timestamp": utcnow_isoformat(),
                })

            build_settings.mark_in_progress()

            return {
                "current_decision": decision,
                "build_in_progress": True,
                "task": {
                    "description": task_description,
                    "target_capabilities": target_capabilities,
                },
                "build_started_at": utcnow_isoformat(),
                "build_settings": build_settings.model_dump(),
            }

        except RuntimeError as e:
            logger.warning("Failed to start async build: %s", e)
            decision = Decision(
                action=DecisionAction.ANSWER_DIRECTLY,
                reasoning="Build already in progress",
                requires_planning=False,
                direct_response=(
                    f"⚠️ Build Already In Progress\n\n"
                    f"{str(e)}\n\n"
                    f"Ask for build status to check progress."
                ),
            )
            return {"current_decision": decision}
    else:
        logger.error("No background runner available; build requests must run in background")
        decision = Decision(
            action=DecisionAction.ANSWER_DIRECTLY,
            reasoning="Background build runner unavailable",
            requires_planning=False,
            direct_response=(
                "⚠️ Background Build Unavailable\n\n"
                "I couldn't start the build because the background runner isn't available. "
                "Please try again in a moment."
            ),
        )
        return {"current_decision": decision}


async def _handle_a2a(
    user_input: str,
    state: "ProgenitorState",
    llm_client,
    tool_registry,
    state_manager,
    decision_data: dict,
    memory_context: str | None,
    progress_message: str | None = None,
) -> "ProgenitorState":
    """Handle delegate_a2a action from LLM."""
    target_capabilities = decision_data.get("target_capabilities", [])

    if not target_capabilities:
        decision = Decision(
            action=DecisionAction.ANSWER_DIRECTLY,
            reasoning="delegate_a2a without target_capabilities - responding directly",
            requires_planning=False,
            direct_response="I wasn't able to determine which agent to use. Could you please clarify your request?",
        )
        return {"current_decision": decision}

    capability = target_capabilities[0]
    agent_config = tool_registry.find_agent_by_capability(capability)
    agent_name = agent_config.get("name", capability) if agent_config else capability

    await _push_activity_event(
        event_type="activity_started",
        content=((progress_message or "").strip() or f"Calling agent: {agent_name}"),
        metadata={
            "activity": "a2a_call",
            "agent_name": agent_name,
            "capability": capability,
        },
    )

    # Execute agent call
    result, error = await _execute_a2a_agent_inline(
        target_capabilities=target_capabilities,
        user_input=user_input,
        tool_registry=tool_registry,
    )

    if error:
        context_data = f"Agent error: {error}"
    else:
        assert result is not None
        context_data = _format_a2a_response(result, agent_name)

    try:
        response_text, was_streamed = await _respond_with_context(
            user_input, f"agent:{agent_name}", context_data, state, llm_client, memory_context
        )
    except Exception as e:
        logger.warning("LLM enrichment failed for A2A call to %s: %s", agent_name, e)
        response_text = context_data
        was_streamed = False

    decision = Decision(
        action=DecisionAction.ANSWER_DIRECTLY,
        reasoning=f"A2A call to {agent_name} (capability: {capability}) with LLM-enriched response",
        requires_planning=False,
        target_capabilities=target_capabilities,
        direct_response=None if was_streamed else response_text,
    )

    if state_manager:
        await state_manager.log_decision({
            "event": "a2a_agent_call",
            "agent_name": agent_name,
            "capability": capability,
            "success": error is None,
            "llm_enriched": True,
            "error": error,
            "timestamp": utcnow_isoformat(),
        })

    return {"current_decision": decision}


async def _handle_mcp(
    user_input: str,
    state: "ProgenitorState",
    llm_client,
    tool_registry,
    state_manager,
    decision_data: dict,
    memory_context: str | None,
    progress_message: str | None = None,
) -> "ProgenitorState":
    """Handle use_mcp action from LLM."""
    target_capabilities = decision_data.get("target_capabilities", [])

    if not target_capabilities:
        decision = Decision(
            action=DecisionAction.ANSWER_DIRECTLY,
            reasoning="use_mcp without target_capabilities - responding directly",
            requires_planning=False,
            direct_response="I wasn't able to determine which tool to use. Could you please clarify your request?",
        )
        return {"current_decision": decision}

    capability = target_capabilities[0]
    tool_config = tool_registry.find_tool_by_capability(capability)

    await _push_activity_event(
        event_type="activity_started",
        content=((progress_message or "").strip() or f"Invoking MCP capability: {capability}"),
        metadata={
            "activity": "mcp_call",
            "capability": capability,
        },
    )

    if not tool_config:
        context_data = f"No MCP tool found for capability: {capability}"
        try:
            response_text, was_streamed = await _respond_with_context(
                user_input, "mcp_lookup", context_data, state, llm_client, memory_context
            )
        except Exception:
            response_text = context_data
            was_streamed = False
    else:
        tool_name = tool_config.get("name", capability)
        from ..tools.mcp import MCPClient

        mcp_client = MCPClient()
        try:
            mcp_result = await mcp_client.call_tool(
                tool_config,
                method="execute",
                params={"input": user_input},
            )
            context_data = json.dumps(mcp_result, indent=2) if isinstance(mcp_result, dict) else str(mcp_result)
        except Exception as e:
            logger.warning("MCP tool execution failed for %s: %s", tool_name, e)
            context_data = f"MCP tool '{tool_name}' execution error: {e}"

        try:
            response_text, was_streamed = await _respond_with_context(
                user_input, f"mcp:{tool_name}", context_data, state, llm_client, memory_context
            )
        except Exception as e:
            logger.warning("LLM enrichment failed for MCP call to %s: %s", tool_name, e)
            response_text = context_data
            was_streamed = False

    decision = Decision(
        action=DecisionAction.ANSWER_DIRECTLY,
        reasoning=f"MCP tool call (capability: {capability}) with LLM-enriched response",
        requires_planning=False,
        target_capabilities=target_capabilities,
        direct_response=None if was_streamed else response_text,
    )

    if state_manager:
        await state_manager.log_decision({
            "event": "mcp_tool_call",
            "capability": capability,
            "llm_enriched": True,
            "timestamp": utcnow_isoformat(),
        })

    return {"current_decision": decision}


async def _execute_a2a_agent_inline(
    target_capabilities: list[str],
    user_input: str,
    tool_registry,
    timeout: float = 60.0,
) -> tuple[dict | None, str | None]:
    """Execute an A2A agent call inline."""
    if not target_capabilities:
        return None, "No target capabilities specified"

    capability = target_capabilities[0]
    agent_config = tool_registry.find_agent_by_capability(capability)

    if not agent_config:
        return None, f"No agent found for capability: {capability}"

    agent_name = agent_config.get("name", "unknown")
    status = agent_config.get("status", "stopped")

    if status == "failed":
        error = agent_config.get("last_error", "Unknown error")
        return None, f"Agent '{agent_name}' is in failed state: {error}"

    if status not in ("running", "waiting", "executing"):
        return None, f"Agent '{agent_name}' is not running (status: {status})"

    a2a_client = A2AClient()
    result = await a2a_client.call_agent(
        agent_config,
        request=user_input,
        timeout=timeout,
    )

    return result, None
