"""
Respond Node - Generates and sends response to user.

Per loop.spec.md: "Final output must include a compact verification summary."

Messages are stored in current_state.json (progenitor agent state).
"""

import uuid
from collections.abc import Callable, Coroutine
from typing import Any, cast

from ..runtime.time_utils import utcnow
from ..state.helpers import get_ideal_state
from ..state.models import ConversationMessage, ProgenitorState, WorkItem


def create_node(state_manager) -> Callable[[ProgenitorState], Coroutine[Any, Any, ProgenitorState]]:
    """Create the respond node function."""

    async def respond_node(state: ProgenitorState) -> ProgenitorState:
        """Generate and prepare response for user.

        Per loop.spec.md: Final output must include compact verification summary.
        If stopped early, return best artefact plus unmet criteria.

        Also handles clarification requests - when the inner loop detects
        that Copilot CLI needs more information, we present the question
        to the user and mark state as awaiting clarification.
        """
        decision = state.get("current_decision")
        current_item = state.get("current_work_item")
        state.get("current_input")

        # Check if this is a clarification request
        clarification_updates: dict[str, Any] = {}
        clarification = state.get("clarification", {}) or {}
        clarification_question = clarification.get("question")
        if clarification.get("needed") and clarification_question:
            # This is a clarification request - present question to user
            response_text = str(clarification_question)
            clarification_updates = {
                "awaiting_clarification": True,
                "clarification": {
                    **clarification,
                    "needed": False,
                    "awaiting": True,
                },
            }

            # Log the clarification request
            message = ConversationMessage(
                id=str(uuid.uuid4()),
                role="assistant",
                content=response_text or "",
                timestamp=utcnow(),
                metadata={
                    "type": "clarification_request",
                    "clarification_context": clarification.get("context"),
                    "iteration": state.get("iteration", {}).get("current", 0),
                },
            )
            await state_manager.log_conversation(message)

            messages = list(state.get("messages", []))
            messages.append(message)

            return cast(
                ProgenitorState,
                {
                    "pending_response": response_text or "",
                    "messages": messages,
                    **clarification_updates,
                },
            )

        # Check if response was already streamed by the chat node
        from ..runtime.loop import get_current_loop

        loop = get_current_loop()
        if loop and loop._response_was_streamed:
            # Response was already streamed, but we still need to save it to messages
            streamed_content = getattr(loop, "_streamed_response_content", "")

            if streamed_content:
                # Log and save the streamed assistant response
                message = ConversationMessage(
                    id=str(uuid.uuid4()),
                    role="assistant",
                    content=streamed_content,
                    timestamp=utcnow(),
                    metadata={
                        "decision_action": _get_decision_action(decision),
                        "work_item_id": None,
                        "verification_score": 0.0,
                        "iteration_count": 0,
                        "stop_reason": None,
                        "streamed": True,
                    },
                )

                await state_manager.log_conversation(message)

                # Add to messages
                messages = list(state.get("messages", []))
                messages.append(message)

                # Persist messages to current_state.json
                await state_manager.save_messages(messages)

                return cast(
                    ProgenitorState,
                    {
                        "pending_response": streamed_content,
                        "messages": messages,
                        "current_input": None,
                    },
                )

            # No content to save, just clear the input
            return cast(
                ProgenitorState,
                {
                    "current_input": None,
                },
            )

        response_text_out: str | None = _generate_response(decision, current_item, state)

        # Don't respond if no meaningful response
        if not response_text_out:
            return {}

        # Add verification summary if we were in inner loop (loop.spec.md requirement)
        verification_summary = _generate_verification_summary(state)
        if verification_summary:
            response_text_out = f"{response_text_out}\n\n{verification_summary}"

        # Log assistant response
        message = ConversationMessage(
            id=str(uuid.uuid4()),
            role="assistant",
            content=response_text_out,
            timestamp=utcnow(),
            metadata={
                "decision_action": _get_decision_action(decision),
                "work_item_id": _get_work_item_id(current_item),
                "verification_score": state.get("verification", {}).get("current_score", 0.0),
                "iteration_count": state.get("iteration", {}).get("current", 0),
                "stop_reason": state.get("iteration_stop_reason"),
            },
        )

        await state_manager.log_conversation(message)

        # Add to messages
        messages = list(state.get("messages", []))
        messages.append(message)

        # Persist messages to current_state.json
        await state_manager.save_messages(messages)

        # Clear build state if we were building (inner loop completed)
        build_clear_updates: dict[str, Any] = {}
        if state.get("build_in_progress") and get_ideal_state(state):
            # Build has completed - clear build state
            build_clear_updates = {
                "build_in_progress": False,
                "build_phase": None,
                # Keep build_task_description and build_started_at for history
                # but mark as no longer active
            }
            # Also clear the spec_doc from build_state.json to avoid stale data on next build
            # Note: The spec file remains under build/ as it's part of the built artifact
            await state_manager.clear_spec_doc()

        return cast(
            ProgenitorState,
            {
                "pending_response": response_text_out,
                "messages": messages,
                "current_input": None,  # Clear input after responding
                **build_clear_updates,
            },
        )

    return respond_node


def _generate_verification_summary(state: ProgenitorState) -> str | None:
    """
    Generate compact verification summary per loop.spec.md.

        Per spec: Final output must include compact verification summary.
    """
    verification_report = state.get("verification", {}).get("current_report")
    stop_reason = state.get("iteration_stop_reason")

    if not verification_report:
        return None

    # Extract report data
    if isinstance(verification_report, dict):
        report_dict = verification_report
    elif hasattr(verification_report, "model_dump"):
        report_dict = verification_report.model_dump()
    else:
        report_dict = verification_report

    score = report_dict.get("overall_score", 0)
    passed = report_dict.get("passed", False)
    gaps = report_dict.get("gaps", [])
    iteration = state.get("iteration", {}).get("current", 0)

    # Build compact summary
    summary_parts = ["---", "Verification Summary"]

    # Score and status
    status_emoji = "✓" if passed else "○"
    summary_parts.append(f"{status_emoji} Score: {score:.2f} | Iterations: {iteration}")

    # Stop reason if not passed
    if stop_reason and not passed:
        summary_parts.append(f"Stopped: {stop_reason}")

    # Unmet criteria (gaps) - per spec requirement
    if gaps:
        summary_parts.append(f"Unmet criteria: {', '.join(gaps[:3])}")
        if len(gaps) > 3:
            summary_parts.append(f"  (+{len(gaps) - 3} more)")

    return "\n".join(summary_parts)


def _generate_response(decision, current_item, state: ProgenitorState) -> str | None:
    """Generate appropriate response text."""
    # Check for direct response from decision
    if decision:
        decision_dict = decision.model_dump() if hasattr(decision, "model_dump") else decision
        user_response = decision_dict.get("user_response") or decision_dict.get("direct_response")
        if user_response:
            return cast(str, user_response)

    # Generate response based on completed work
    if current_item:
        response = _format_work_item_response(current_item)
        return response

    # Check for pending work
    backlog = state.get("backlog", [])
    pending_count = _count_pending(backlog)

    if pending_count > 0:
        if state.get("current_input"):
            return "Queued. I’ll send the update soon."
        return None

    user_input = state.get("current_input")
    if user_input:
        return "I've noted your request. Let me know if you need anything else."

    # Idle state - no response
    return None


def _format_work_item_response(current_item) -> str | None:
    """Format response for a work item."""
    if isinstance(current_item, WorkItem):
        status = current_item.status
        description = current_item.description
        error_msg = current_item.error_message
        result = current_item.result or {}
    elif isinstance(current_item, dict):
        status = current_item.get("status", "")
        description = current_item.get("description", "")
        error_msg = current_item.get("error_message")
        result = current_item.get("result", {})
    else:
        return None

    if status == "completed":
        if result.get("user_notified"):
            return None
        response_text = result.get("response")
        if response_text:
            response_lower = response_text.strip().lower()
            if response_lower.startswith("say:"):
                return cast(str, response_text[4:].strip())
            if response_lower.startswith("ask:"):
                return cast(str, response_text[4:].strip())
            if response_lower.startswith("notify:"):
                # Strip directive and level if present: "NOTIFY: level | message"
                notify_body = response_text[7:].strip()
                if "|" in notify_body:
                    notify_body = notify_body.split("|", 1)[1].strip()
                return cast(str, notify_body)
            return cast(str, response_text)
        elif result.get("message"):
            return f"✓ {result['message']}"
        else:
            return f"✓ Completed: {description}"
    elif status == "failed":
        return f"✗ Failed: {description}\nError: {error_msg}"

    return None


def _count_pending(backlog: list) -> int:
    """Count pending items in backlog."""
    count = 0
    for item in backlog:
        if isinstance(item, WorkItem):
            if item.is_pending:
                count += 1
        elif isinstance(item, dict) and item.get("status") == "pending":
            count += 1
    return count


def _get_decision_action(decision) -> str | None:
    """Extract decision action value."""
    if not decision:
        return None
    if hasattr(decision, "action"):
        action = decision.action
        return action.value if hasattr(action, "value") else str(action)
    if isinstance(decision, dict):
        action = decision.get("action")
        return action if isinstance(action, str) else str(action) if action is not None else None
    return None


def _get_work_item_id(current_item) -> str | None:
    """Extract work item ID."""
    if not current_item:
        return None
    if isinstance(current_item, WorkItem):
        return current_item.id
    if isinstance(current_item, dict):
        work_item_id = current_item.get("id")
        return (
            work_item_id
            if isinstance(work_item_id, str)
            else str(work_item_id)
            if work_item_id is not None
            else None
        )
    return None
