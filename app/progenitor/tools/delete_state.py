"""
Delete State Tool - remove an entire runtime state document from the store.

The native file tools have no delete operation, so this is how the agent
removes a whole per-id document (e.g. a single ``kanban_card:<id>``). Note: for
documents that hold multiple entries (e.g. ``mcp.json``'s ``mcpServers`` map or
the object-keyed-by-id ``schedule.json``), removing one entry is a native
``write``/``edit`` that deletes that one key - ``delete_state`` wipes the
WHOLE document, not one entry within it.
"""

import logging

from pydantic import BaseModel, Field

from .protocol import BaseSystemTool, ToolContext, ToolResult

logger = logging.getLogger(__name__)


class DeleteStateArgs(BaseModel):
    """Arguments for the delete_state tool."""

    document: str = Field(
        description=(
            "Name of the state document to delete entirely (e.g. a "
            "'kanban_card:<id>' document). For multi-entry documents like "
            "mcp.json or schedule.json, remove a single entry (key) with a "
            "native write/edit instead."
        )
    )


class DeleteStateTool(BaseSystemTool):
    """Delete an entire runtime state document."""

    @property
    def args_model(self) -> type[BaseModel]:
        return DeleteStateArgs

    @property
    def name(self) -> str:
        return "delete_state"

    @property
    def description(self) -> str:
        return (
            "Delete an ENTIRE runtime state document from the store. Use for "
            "removing a whole document (e.g. a single 'kanban_card:<id>'). To "
            "remove one entry from a multi-entry document (like mcp.json or "
            "schedule.json), delete that one key with the native write/edit "
            "file tools instead."
        )

    async def execute(self, context: ToolContext) -> ToolResult:
        if context.state_manager is None:
            return ToolResult.fail("state_manager unavailable; cannot delete state documents")

        args = context.tool_args if isinstance(context.tool_args, dict) else {}
        document = str(args.get("document") or "").strip()
        if not document:
            return ToolResult.fail("Missing 'document' (the state document name)")

        existed = await context.state_manager.get_state_document(document, default=None) is not None
        await context.state_manager.delete_state_document(document)
        await _emit_state_deleted_event(context, document)

        return ToolResult.ok(
            {
                "success": True,
                "document": document,
                "deleted": existed,
                "action": "deleted" if existed else "noop",
            }
        )


async def _emit_state_deleted_event(context: ToolContext, document: str) -> None:
    """Best-effort notify subscribers that a state document was deleted."""
    push = getattr(context.llm_client, "_push_event", None)
    if push is None:
        return
    try:
        await push(
            "state_document_updated",
            f"State document '{document}' deleted",
            {"document": document, "deleted": True},
        )
    except Exception:  # pragma: no cover - notification is best-effort
        logger.debug("Failed to emit state_document_updated (delete) event", exc_info=True)
