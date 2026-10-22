"""Persisted routine lifecycle and commit-time checkpoint notifications."""

from collections.abc import Awaitable, Callable
from typing import Any, cast

from ..utils import utcnow_isoformat

RUN_EVENT_PREFIX = "routine_run_event:"
CHECKPOINT_CHANNEL = "routine_checkpoints"

# Keep LangGraph's schema and checkpoint format intact. NOTIFY contains only
# identifiers and is delivered after commit; readers use the native saver.
# Sent as one simple query (a single implicit transaction): the xact lock
# serializes concurrent startups, which otherwise race on the function/trigger
# catalog rows ("tuple concurrently updated" / "trigger already exists").
CHECKPOINT_TRIGGER_SQL = """
SELECT pg_advisory_xact_lock(hashtext('progena_routine_checkpoint_trigger'));
CREATE OR REPLACE FUNCTION progena_notify_routine_checkpoint()
RETURNS trigger AS $$
BEGIN
    IF NEW.thread_id LIKE 'routine:%' AND NEW.checkpoint_ns = '' THEN
        PERFORM pg_notify('routine_checkpoints',
            json_build_object('thread_id', NEW.thread_id)::text);
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE OR REPLACE TRIGGER progena_routine_checkpoint
AFTER INSERT OR UPDATE ON checkpoints FOR EACH ROW
EXECUTE FUNCTION progena_notify_routine_checkpoint();
"""


async def persist_run_event(state_manager: Any, payload: dict[str, Any], status: str) -> None:
    """Write an immutable transition, so fast runs cannot erase 'started'."""
    writer = getattr(state_manager, "save_routine_run_event", None)
    if callable(writer):
        save_event = cast(Callable[[dict[str, Any]], Awaitable[None]], writer)
        await save_event({**payload, "status": status, "timestamp": utcnow_isoformat()})


def _message_text(message: Any) -> str:
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return str(content)


def routine_run_output(output_schema: Any, result: Any) -> dict[str, Any]:
    """The value a completed run hands back to its caller (e.g. a workflow).

    A routine whose ``output_schema`` declares properties returns those fields
    (the compiler's output projector puts them at the result's top level);
    otherwise it returns ``message``, the text of its final AI message.
    """
    properties = output_schema.get("properties") if isinstance(output_schema, dict) else None
    values = result if isinstance(result, dict) else {}
    if isinstance(properties, dict) and properties:
        return {name: values[name] for name in properties if name in values}
    messages = values.get("messages") or []
    final = next(
        (m for m in reversed(messages) if getattr(m, "type", None) == "ai"),
        None,
    )
    return {"message": _message_text(final) if final is not None else ""}
