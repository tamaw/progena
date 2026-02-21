"""
Decide - Build routing helper (runtime utility).

Invoked as a LangGraph node to determine whether a request is a build
(agent or MCP), extract build settings, and kick off a background build
while immediately returning an acknowledgement message to the user.

For all non-build actions (ANSWER_DIRECTLY, SYSTEM_TOOL, DELEGATE_A2A,
USE_MCP) the node returns Decision(action=<action>, direct_response=None)
so the chat loop can handle the response via tool calls.
"""

import inspect
import json
import logging
import re
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any, cast

from openai import APIConnectionError, RateLimitError
from pydantic import BaseModel, Field

from ..config import DEFAULT_BUILD_CONFIDENCE_THRESHOLD
from ..llm.prompts import PromptTemplates
from ..llm.response_models import DecisionResponse, validate_decision_response
from ..state.models import (
    Decision,
    DecisionAction,
    ProgenitorState,
)
from .time_utils import utcnow_isoformat

if TYPE_CHECKING:
    from ..state.execution_schemas import BuildSettings

logger = logging.getLogger(__name__)

_prompts = PromptTemplates()


# ---------------------------------------------------------------------------
# Build name / type extraction helpers
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


# ---------------------------------------------------------------------------
# LLM decision request
# ---------------------------------------------------------------------------

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
            "DECIDE: action=%s confidence=%s targets=%s",
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
            )
            validated = validate_decision_response(parsed.model_dump())
            decision = validated.model_dump()
            logger.info(
                "DECIDE: action=%s confidence=%s targets=%s",
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
        )
        parsed = json.loads(response)
        validated = validate_decision_response(parsed)
        decision = validated.model_dump()
        logger.info(
            "DECIDE: action=%s confidence=%s targets=%s",
            decision.get("action"),
            decision.get("confidence"),
            decision.get("target_capabilities"),
        )
        return decision
    except Exception as exc:
        logger.warning("Failed to get structured decide response: %s", exc)
        validated = validate_decision_response({})
        return validated.model_dump()


# ---------------------------------------------------------------------------
# Build settings extraction
# ---------------------------------------------------------------------------

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

    # The action is authoritative for build_type when present.
    llm_action = (llm_action or "").lower().strip()
    if llm_action == "build_mcp":
        build_type = BuildType.MCP
    elif llm_action == "build_progenitor":
        build_type = BuildType.PROGENITOR
        project_name = "progenitor"

    # Primary: use values from the decision response if available
    if decision_data:
        decision_project_name = decision_data.get("project_name")
        decision_build_type = decision_data.get("build_type")
        decision_build_description = decision_data.get("build_description")

        if decision_project_name:
            project_name = _clean_project_name(decision_project_name)
            if project_name:
                logger.info("Using project_name from decision response: %s", project_name)

        if decision_build_type:
            match decision_build_type.lower():
                case "mcp":
                    build_type = BuildType.MCP
                case "progenitor":
                    build_type = BuildType.PROGENITOR
                    project_name = "progenitor"
            logger.info("Using build_type from decision response: %s", build_type)

        if decision_build_description:
            build_description = decision_build_description

    # Fallback: LLM structured output if decision did not provide project_name
    if not project_name and llm_client and build_type != BuildType.PROGENITOR:
        try:
            system_prompt = _prompts.get("builder_agent/decide_project_name_system.md")
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
                    actual_prompt = (
                        prompt
                        + "\n\nRespond with valid JSON matching this schema: "
                        "{project_name: string, build_type: 'agent'|'mcp', reasoning: string}"
                    )

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

    # Fallback: regex patterns
    if not project_name and build_type != BuildType.PROGENITOR:
        text = user_input.lower()
        name_patterns = [
            r'\b(?:build|create|scaffold|generate|make|implement)\s+(?:for\s+me\s+)?(?:me\s+)?(?:please\s+)?(?:a\s+|an\s+)?(?:new\s+)?(?:simple\s+|basic\s+)?([a-z][a-z0-9]*(?:\s+[a-z][a-z0-9]*)?)\s+(?:a2a\s+)?(?:agent|mcp|progenitor)',
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
    if "progenitor" in text or "app/progenitor" in text:
        build_type = BuildType.PROGENITOR
        project_name = "progenitor"

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
# Conversation history extractor (shared utility)
# ---------------------------------------------------------------------------

def _extract_history(
    messages: list,
    limit: int = 10,
    memory_context: str | None = None,
    exclude_latest_user_input: str | None = None,
) -> list[dict[str, str]]:
    """Extract conversation history as a list of role/content dicts."""
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
        elif hasattr(msg, "role"):
            raw_role = msg.role
            role = raw_role if raw_role in allowed_roles else "user"
            content = msg.content
        else:
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


# ---------------------------------------------------------------------------
# Build action handler
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
    """Handle build_agent / build_mcp / build_progenitor actions from LLM."""

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
            reasoning=f"Build confidence ({confidence:.2f}) below threshold ({build_confidence_threshold})",
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

        return cast(ProgenitorState, {"current_decision": decision})

    # Reject if a build is already in progress
    if state.get("build_in_progress", False):
        current_task = state.get("build_task_description", "unknown task")
        phase = state.get("build_phase", "unknown")
        decision = Decision(
            action=DecisionAction.ANSWER_DIRECTLY,
            reasoning="Build already in progress - rejecting new build request.",
            requires_planning=False,
            direct_response=(
                f"**Build Already In Progress**\n\n"
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

        return cast(ProgenitorState, {"current_decision": decision})

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

    from .background_build import get_background_runner

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

            logger.info(
                "Started async build %s for: %s (confidence=%.2f)",
                build_id, task_description, confidence,
            )

            decision = Decision(
                action=DecisionAction.ANSWER_DIRECTLY,
                reasoning=f"Started async build {build_id} (confidence={confidence:.2f})",
                requires_planning=False,
                direct_response=user_response or ack_message,
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

            return cast(ProgenitorState, {
                "current_decision": decision,
                "build_in_progress": True,
                "task": {
                    "description": task_description,
                    "target_capabilities": target_capabilities,
                },
                "build_started_at": utcnow_isoformat(),
                "build_settings": build_settings.model_dump(),
            })

        except RuntimeError as e:
            logger.warning("Failed to start async build: %s", e)
            decision = Decision(
                action=DecisionAction.ANSWER_DIRECTLY,
                reasoning="Build already in progress",
                requires_planning=False,
                direct_response=(
                    f"**Build Already In Progress**\n\n{str(e)}\n\n"
                    f"Ask for build status to check progress."
                ),
            )
            return cast(ProgenitorState, {"current_decision": decision})
    else:
        logger.error("No background runner available")
        decision = Decision(
            action=DecisionAction.ANSWER_DIRECTLY,
            reasoning="Background build runner unavailable",
            requires_planning=False,
            direct_response=(
                "**Background Build Unavailable**\n\n"
                "I couldn't start the build because the background runner isn't available. "
                "Please try again in a moment."
            ),
        )
        return cast(ProgenitorState, {"current_decision": decision})


# ---------------------------------------------------------------------------
# Node factory
# ---------------------------------------------------------------------------

def create_node(
    llm_client,
    tool_registry,
    state_manager=None,
    config=None,
) -> Callable[[ProgenitorState], Coroutine[Any, Any, ProgenitorState]]:
    """Create the decide node function.

    Single responsibility: detect build requests and dispatch them to the
    background runner. For all other actions (ANSWER_DIRECTLY, SYSTEM_TOOL,
    DELEGATE_A2A, USE_MCP) the node returns a Decision with direct_response=None
    and the chat loop handles the actual response via tool calls.

    Args:
        llm_client: LLM client for making decisions.
        tool_registry: Registry of available tools and agents.
        state_manager: Optional state manager for logging decisions.
        config: Optional ProgenitorConfig for runtime settings.
    """
    build_confidence_threshold = DEFAULT_BUILD_CONFIDENCE_THRESHOLD
    if config and hasattr(config, "runtime"):
        build_confidence_threshold = getattr(
            config.runtime, "build_confidence_threshold", DEFAULT_BUILD_CONFIDENCE_THRESHOLD
        )

    async def decide_node(state: ProgenitorState) -> ProgenitorState:
        """Classify the current input and route build requests to the runner.

        Returns a partial state update with ``current_decision`` set:
        - Build actions: decision carries an ack message and the build is started.
        - All other actions: decision has ``direct_response=None``; the chat loop responds.
        """
        user_input = state.get("current_input")

        if not user_input:
            return cast(ProgenitorState, {"current_decision": None})

        tool_context = tool_registry.get_tool_context()
        messages = state.get("messages", [])
        history = _extract_history(messages, limit=10, exclude_latest_user_input=user_input)

        try:
            decision_data = await _request_decision_data(
                llm_client=llm_client,
                user_input=user_input,
                conversation_history=history,
                tool_context=tool_context,
            )

            action_str = decision_data.get("action", "answer_directly")
            confidence = decision_data.get("confidence", 0.5)
            user_response = decision_data.get("user_response") or decision_data.get("direct_response")

            llm_action = str(action_str).lower().strip()
            is_build_action = llm_action in ("build_agent", "build_mcp", "build_progenitor")

            if is_build_action:
                return await _handle_build_action(
                    user_input, state, llm_client, tool_registry, state_manager,
                    decision_data, llm_action, confidence, build_confidence_threshold,
                    user_response,
                )

            # Not a build — let the chat loop handle the response.
            try:
                action = DecisionAction(action_str)
            except ValueError:
                logger.warning("Unknown action '%s', defaulting to answer_directly", action_str)
                action = DecisionAction.ANSWER_DIRECTLY

            decision = Decision(
                action=action,
                reasoning=decision_data.get("reasoning", ""),
                requires_planning=False,
                target_capabilities=decision_data.get("target_capabilities", []),
                direct_response=None,
            )

            if state_manager:
                await state_manager.log_decision({
                    "event": "routing_decision",
                    "action": action.value,
                    "confidence": confidence,
                    "reasoning": decision_data.get("reasoning", ""),
                    "target_capabilities": decision_data.get("target_capabilities", []),
                    "timestamp": utcnow_isoformat(),
                })

            return cast(ProgenitorState, {"current_decision": decision})

        except (RateLimitError, APIConnectionError) as e:
            logger.warning("Recoverable LLM error in decide node: %s", e)

            decision = Decision(
                action=DecisionAction.ANSWER_DIRECTLY,
                reasoning=f"LLM API error: {e}",
                requires_planning=False,
                direct_response=(
                    "**Temporary Issue**\n\n"
                    f"I encountered an issue connecting to the AI service: {type(e).__name__}\n\n"
                    "This is usually temporary. Please try again in a moment."
                ),
            )

            if state_manager:
                await state_manager.log_decision({
                    "event": "routing_decision_error",
                    "error_type": type(e).__name__,
                    "error_message": str(e),
                    "recoverable": True,
                    "timestamp": utcnow_isoformat(),
                })

            return cast(ProgenitorState, {"current_decision": decision})

        except Exception as e:
            logger.error("Unexpected error in decide node: %s", e, exc_info=True)

            decision = Decision(
                action=DecisionAction.ANSWER_DIRECTLY,
                reasoning=f"Fallback due to error: {type(e).__name__}: {e}",
                requires_planning=False,
                direct_response=(
                    "**Error Processing Request**\n\n"
                    f"I encountered an unexpected error while processing your request:\n"
                    f"{type(e).__name__}: {str(e)[:200]}\n\n"
                    "This has been logged. You can try rephrasing your request or ask for help."
                ),
            )

            if state_manager:
                await state_manager.log_decision({
                    "event": "routing_decision_error",
                    "error_type": type(e).__name__,
                    "error_message": str(e),
                    "recoverable": False,
                    "timestamp": utcnow_isoformat(),
                })

            return cast(ProgenitorState, {"current_decision": decision})

    return decide_node
