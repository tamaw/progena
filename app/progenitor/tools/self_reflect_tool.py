"""
Self Reflect Tool - Introspect own source code to understand how Progenitor works.
"""

import logging

from ..nodes.node_helpers.protocol import BaseSystemTool, ToolContext, ToolResult

logger = logging.getLogger(__name__)


class SelfReflectTool(BaseSystemTool):
    """Introspect own source code to understand how Progenitor works."""

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
        from .self_reflect import SelfReflection

        logger.info("Executing system tool: self_reflect")
        reflection = SelfReflection(llm_client=context.llm_client)

        # For inline execution, return module listing
        modules = reflection.list_modules()

        return ToolResult.ok({
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
        })
