"""
Self Reflect Tool - Introspect own source code to understand how Progenitor works.
"""

import logging
from typing import Any

from pydantic import BaseModel

from .protocol import BaseSystemTool, ToolContext, ToolResult

logger = logging.getLogger(__name__)


def _build_artifact(operation: str, result: dict[str, Any]) -> dict[str, Any] | None:
    """Project a self_reflect result into a small activity-feed summary.

    ``kind="file"`` matches the convention ``stream_events._extract_artifact``
    already expects from file reads (it appends "N more lines not shown" when
    the SSE preview cuts a file short); the other operations get an analogous
    one-line summary of what was found.
    """
    if operation == "read":
        content = result.get("content")
        lines = content.count("\n") + 1 if isinstance(content, str) and content else 0
        return {
            "kind": "file",
            "count": lines,
            "summary": f"{result.get('path', '')} ({result.get('size', 0)} bytes, {lines} lines)",
        }
    if operation == "explore":
        return {
            "kind": "directory",
            "count": result.get("total_files", 0) + result.get("total_dirs", 0),
            "summary": (
                f"{result.get('directory', '.')}: {result.get('total_files', 0)} files, "
                f"{result.get('total_dirs', 0)} dirs"
            ),
        }
    if operation == "list_modules":
        return {
            "kind": "directory",
            "count": result.get("total", 0),
            "summary": "top-level modules",
        }
    if operation == "search":
        matches = result.get("total_matches", 0)
        return {
            "kind": "search",
            "count": matches,
            "summary": f"{matches} matches for '{result.get('query', '')}' "
            f"across {result.get('files_searched', 0)} files",
        }
    if operation == "find_relevant":
        return {
            "kind": "search",
            "count": result.get("count", 0),
            "summary": f"{result.get('count', 0)} files relevant to '{result.get('topic', '')}'",
        }
    if operation == "module_overview":
        return {"kind": "directory", "summary": f"overview of module '{result.get('module', '')}'"}
    return None


class SelfReflectTool(BaseSystemTool):
    """Introspect own source code to understand how Progenitor works."""

    @property
    def args_model(self) -> type[BaseModel]:
        from .self_reflect import SelfReflectArgs

        return SelfReflectArgs

    @property
    def name(self) -> str:
        return "self_reflect"

    @property
    def description(self) -> str:
        return "Introspect own source code to understand how Progenitor works"

    async def execute(self, context: ToolContext) -> ToolResult:
        from .self_reflect import SelfReflection, execute_self_reflect

        logger.info("Executing system tool: self_reflect")
        args = context.tool_args if isinstance(context.tool_args, dict) else {}
        operation = str(args.get("operation") or "").strip()
        if operation:
            is_regex_arg = args.get("is_regex", False)
            result = await execute_self_reflect(
                operation=operation,
                directory=str(args.get("directory") or ""),
                file_path=str(args.get("file_path") or ""),
                query=str(args.get("query") or ""),
                topic=str(args.get("topic") or ""),
                module_name=str(args.get("module_name") or ""),
                is_regex=(
                    is_regex_arg
                    if isinstance(is_regex_arg, bool)
                    else str(is_regex_arg).strip().lower() in {"1", "true", "yes", "on"}
                ),
                max_lines=(int(args["max_lines"]) if args.get("max_lines") is not None else None),
                max_results=int(args.get("max_results") or 10),
            )
            return ToolResult.ok(result, artifact=_build_artifact(operation, result))

        reflection = SelfReflection(llm_client=context.llm_client)

        # For inline execution, return module listing
        modules = reflection.list_modules()

        return ToolResult.ok(
            {
                "description": "Progenitor self-reflection - exploring own source code",
                "root_path": str(reflection.root),
                "modules": [
                    {
                        "name": m.name,
                        "path": m.path,
                        "is_directory": m.is_directory,
                        "description": m.description,
                    }
                    for m in modules
                ],
                "module_count": len([m for m in modules if m.is_directory]),
                "file_count": len([m for m in modules if not m.is_directory]),
                "tip": "Use follow-up questions to explore specific modules or search for code patterns.",
            }
        )
