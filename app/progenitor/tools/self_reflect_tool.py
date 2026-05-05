"""
Self Reflect Tool - Introspect own source code to understand how Progenitor works.
"""

import logging

from pydantic import BaseModel

from .protocol import BaseSystemTool, ToolContext, ToolResult

logger = logging.getLogger(__name__)


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

    @property
    def keywords(self) -> list[str]:
        return [
            "self reflect",
            "self-reflect",
            "introspect",
            "understand yourself",
            "examine yourself",
            "look at your code",
            "read your code",
            "your own code",
            "your source code",
            "your implementation",
            "your architecture",
            "how are you built",
            "how were you built",
            "how are you implemented",
        ]

    @property
    def trigger_patterns(self) -> list[str]:
        return [
            r"\bhow do you (?:work|handle|process)\b",
            r"\b(?:explain|describe) your\b",
        ]

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
            return ToolResult.ok(result)

        reflection = SelfReflection()

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
