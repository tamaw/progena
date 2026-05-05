"""
Self-Reflection Tool - Allows Progenitor to introspect its own source code.

This tool enables the agent to read and understand its own implementation
by exploring directories, searching for relevant code, and reading files.
All operations are READ-ONLY to ensure safety.
"""

import logging
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from .protocol import BaseSystemTool, ToolContext, ToolResult, build_tool_spec

logger = logging.getLogger(__name__)

# Root of the progenitor source code
PROGENITOR_ROOT = Path(__file__).parent.parent  # app/progenitor/

# Directories to explore (relative to PROGENITOR_ROOT)
EXPLORABLE_DIRS = [
    "",  # Root of progenitor
    "nodes",
    "state",
    "tools",
    "llm",
    "runtime",
    "ipc",
]

# File extensions to include
ALLOWED_EXTENSIONS = {".py", ".md", ".txt", ".json"}

# Files/folders to skip
SKIP_ITEMS = {"__pycache__", ".pyc", ".pyo", "__init__.py"}

# Maximum file size to read (prevent memory issues)
MAX_FILE_SIZE = 50_000  # 50KB

# Maximum total content to return in a single operation
MAX_TOTAL_CONTENT = 100_000  # 100KB


class DirectoryEntry(BaseModel):
    """A file or directory entry."""

    name: str
    path: str  # Relative to progenitor root
    is_directory: bool
    size: int | None = None
    description: str | None = None


class DirectoryListing(BaseModel):
    """Result of listing a directory."""

    directory: str
    entries: list[DirectoryEntry]
    total_files: int
    total_dirs: int


class FileContent(BaseModel):
    """Content of a file."""

    path: str
    content: str
    size: int
    truncated: bool = False
    language: str = "python"


class SearchResult(BaseModel):
    """A search match in a file."""

    file_path: str
    line_number: int
    line_content: str
    context_before: list[str] = Field(default_factory=list)
    context_after: list[str] = Field(default_factory=list)


class SearchResults(BaseModel):
    """Results of searching across files."""

    query: str
    matches: list[SearchResult]
    total_matches: int
    files_searched: int


class SelfReflectArgs(BaseModel):
    """Structured arguments for read-only self-reflection operations."""

    operation: Literal[
        "list_modules",
        "explore",
        "read",
        "search",
        "find_relevant",
        "module_overview",
    ] = Field(description="The operation to perform")
    directory: str | None = Field(
        default=None,
        description="Directory path for 'explore' operation (relative to app/progenitor/)",
    )
    file_path: str | None = Field(
        default=None,
        description="File path for 'read' operation (relative to app/progenitor/)",
    )
    query: str | None = Field(default=None, description="Search query for 'search' operation")
    topic: str | None = Field(
        default=None,
        description="Topic for 'find_relevant' operation (e.g., 'user input', 'scheduling')",
    )
    module_name: str | None = Field(
        default=None,
        description="Module name for 'module_overview' operation (e.g., 'nodes', 'tools')",
    )
    is_regex: bool = Field(
        default=False,
        description="Whether search query is a regex (for 'search' operation)",
    )
    max_lines: int | None = Field(
        default=None,
        description="Maximum lines to read (for 'read' operation)",
    )
    max_results: int = Field(
        default=10,
        description="Maximum results to return (for 'search' and 'find_relevant' operations)",
    )


class SelfReflection:
    """
    Self-reflection tool for Progenitor to understand its own code.

    Provides read-only access to:
    - List directories and files
    - Read file contents
    - Search for patterns in code
    """

    def __init__(self):
        """Initialize self-reflection tool."""
        self.root = PROGENITOR_ROOT

    def _is_safe_path(self, path: Path) -> bool:
        """Check if a path is within the allowed progenitor source tree."""
        try:
            resolved = path.resolve()
            root_resolved = self.root.resolve()
            return str(resolved).startswith(str(root_resolved))
        except Exception:
            return False

    def _get_relative_path(self, path: Path) -> str:
        """Get path relative to progenitor root."""
        try:
            return str(path.relative_to(self.root))
        except ValueError:
            return str(path)

    def _get_file_description(self, path: Path) -> str | None:
        """Extract a brief description from a Python file's docstring."""
        if path.suffix != ".py":
            return None
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")[:2000]
            # Look for module docstring
            match = re.search(r'^"""(.+?)"""', content, re.DOTALL)
            if match:
                docstring = match.group(1).strip()
                # Get first line or first sentence
                first_line = docstring.split("\n")[0].strip()
                if len(first_line) > 100:
                    first_line = first_line[:100] + "..."
                return first_line
        except OSError:
            return None
        return None

    def list_modules(self) -> list[DirectoryEntry]:
        """
        List all top-level modules/directories in Progenitor.

        Returns:
            List of directory entries for main modules
        """
        entries = []

        for item in sorted(self.root.iterdir()):
            if item.name in SKIP_ITEMS:
                continue
            if item.name.startswith("."):
                continue

            entry = DirectoryEntry(
                name=item.name,
                path=self._get_relative_path(item),
                is_directory=item.is_dir(),
                size=item.stat().st_size if item.is_file() else None,
                description=self._get_file_description(item) if item.is_file() else None,
            )
            entries.append(entry)

        return entries

    def list_directory(self, relative_path: str = "") -> DirectoryListing:
        """
        List contents of a directory within the progenitor source.

        Args:
            relative_path: Path relative to app/progenitor/

        Returns:
            DirectoryListing with entries
        """
        target = self.root / relative_path

        if not self._is_safe_path(target):
            raise ValueError(f"Path not allowed: {relative_path}")

        if not target.exists():
            raise FileNotFoundError(f"Directory not found: {relative_path}")

        if not target.is_dir():
            raise ValueError(f"Not a directory: {relative_path}")

        entries = []
        total_files = 0
        total_dirs = 0

        for item in sorted(target.iterdir()):
            if item.name in SKIP_ITEMS:
                continue
            if item.name.startswith("."):
                continue

            is_dir = item.is_dir()
            if is_dir:
                total_dirs += 1
            else:
                # Check extension
                if item.suffix not in ALLOWED_EXTENSIONS:
                    continue
                total_files += 1

            entry = DirectoryEntry(
                name=item.name,
                path=self._get_relative_path(item),
                is_directory=is_dir,
                size=item.stat().st_size if not is_dir else None,
                description=self._get_file_description(item) if not is_dir else None,
            )
            entries.append(entry)

        return DirectoryListing(
            directory=relative_path or ".",
            entries=entries,
            total_files=total_files,
            total_dirs=total_dirs,
        )

    def read_file(self, relative_path: str, max_lines: int | None = None) -> FileContent:
        """
        Read a file from the progenitor source tree.

        Args:
            relative_path: Path relative to app/progenitor/
            max_lines: Maximum number of lines to read (None = all)

        Returns:
            FileContent with the file's content
        """
        target = self.root / relative_path

        if not self._is_safe_path(target):
            raise ValueError(f"Path not allowed: {relative_path}")

        if not target.exists():
            raise FileNotFoundError(f"File not found: {relative_path}")

        if not target.is_file():
            raise ValueError(f"Not a file: {relative_path}")

        if target.suffix not in ALLOWED_EXTENSIONS:
            raise ValueError(f"File type not allowed: {target.suffix}")

        size = target.stat().st_size
        truncated = False

        if size > MAX_FILE_SIZE:
            truncated = True
            content = target.read_text(encoding="utf-8", errors="ignore")[:MAX_FILE_SIZE]
            content += (
                f"\n\n... (file truncated at {MAX_FILE_SIZE} bytes, total size: {size} bytes)"
            )
        else:
            content = target.read_text(encoding="utf-8", errors="ignore")

        if max_lines is not None and max_lines > 0:
            lines = content.split("\n")
            if len(lines) > max_lines:
                truncated = True
                content = "\n".join(lines[:max_lines])
                content += f"\n\n... (showing first {max_lines} of {len(lines)} lines)"

        # Detect language
        lang_map = {".py": "python", ".md": "markdown", ".json": "json", ".txt": "text"}
        language = lang_map.get(target.suffix, "text")

        return FileContent(
            path=relative_path,
            content=content,
            size=size,
            truncated=truncated,
            language=language,
        )

    def search_code(
        self,
        query: str,
        directory: str = "",
        is_regex: bool = False,
        case_sensitive: bool = False,
        context_lines: int = 2,
        max_results: int = 20,
    ) -> SearchResults:
        """
        Search for a pattern across files in the progenitor source.

        Args:
            query: Search query (text or regex)
            directory: Directory to search in (relative to progenitor root)
            is_regex: Whether query is a regex pattern
            case_sensitive: Whether search is case-sensitive
            context_lines: Number of context lines before/after match
            max_results: Maximum number of matches to return

        Returns:
            SearchResults with all matches
        """
        target_dir = self.root / directory

        if not self._is_safe_path(target_dir):
            raise ValueError(f"Path not allowed: {directory}")

        if not target_dir.exists() or not target_dir.is_dir():
            raise ValueError(f"Invalid directory: {directory}")

        # Compile pattern
        flags = 0 if case_sensitive else re.IGNORECASE
        if is_regex:
            try:
                pattern = re.compile(query, flags)
            except re.error as e:
                raise ValueError(f"Invalid regex: {e}")
        else:
            pattern = re.compile(re.escape(query), flags)

        matches = []
        files_searched = 0

        for py_file in target_dir.rglob("*.py"):
            if not self._is_safe_path(py_file):
                continue
            if any(skip in str(py_file) for skip in SKIP_ITEMS):
                continue

            files_searched += 1

            try:
                lines = py_file.read_text(encoding="utf-8", errors="ignore").split("\n")

                for i, line in enumerate(lines):
                    if pattern.search(line):
                        # Get context
                        start = max(0, i - context_lines)
                        end = min(len(lines), i + context_lines + 1)

                        match = SearchResult(
                            file_path=self._get_relative_path(py_file),
                            line_number=i + 1,
                            line_content=line.strip(),
                            context_before=[
                                context_line.strip() for context_line in lines[start:i]
                            ],
                            context_after=[
                                context_line.strip() for context_line in lines[i + 1 : end]
                            ],
                        )
                        matches.append(match)

                        if len(matches) >= max_results:
                            break

            except Exception as e:
                logger.debug("Error searching %s: %s", py_file, e)
                continue

            if len(matches) >= max_results:
                break

        return SearchResults(
            query=query,
            matches=matches,
            total_matches=len(matches),
            files_searched=files_searched,
        )

    def find_relevant_files(
        self,
        topic: str,
        max_files: int = 10,
    ) -> list[DirectoryEntry]:
        """
        Find files that might be relevant to a topic based on filename and docstrings.

        Args:
            topic: Topic to search for (e.g., "user input", "scheduling", "LLM")
            max_files: Maximum number of files to return

        Returns:
            List of relevant file entries
        """
        topic_lower = topic.lower()
        topic_words = set(topic_lower.split())

        scored_files: list[tuple[float, DirectoryEntry]] = []

        for py_file in self.root.rglob("*.py"):
            if not self._is_safe_path(py_file):
                continue
            if any(skip in str(py_file) for skip in SKIP_ITEMS):
                continue

            score = 0.0
            rel_path = self._get_relative_path(py_file)

            # Score based on filename
            filename_lower = py_file.stem.lower()
            for word in topic_words:
                if word in filename_lower:
                    score += 2.0

            # Score based on path components
            path_lower = rel_path.lower()
            for word in topic_words:
                if word in path_lower:
                    score += 1.0

            # Score based on docstring
            desc = self._get_file_description(py_file)
            if desc:
                desc_lower = desc.lower()
                for word in topic_words:
                    if word in desc_lower:
                        score += 1.5

            # Additional keyword matching in file content (first 2KB)
            try:
                content = py_file.read_text(encoding="utf-8", errors="ignore")[:2000].lower()
                for word in topic_words:
                    if len(word) > 3:  # Only meaningful words
                        count = content.count(word)
                        score += min(count * 0.1, 1.0)  # Cap per-word score
            except OSError:
                content = ""

            if score > 0:
                entry = DirectoryEntry(
                    name=py_file.name,
                    path=rel_path,
                    is_directory=False,
                    size=py_file.stat().st_size,
                    description=desc,
                )
                scored_files.append((score, entry))

        # Sort by score descending
        scored_files.sort(key=lambda x: x[0], reverse=True)

        return [entry for _, entry in scored_files[:max_files]]

    def get_module_overview(self, module_name: str) -> dict[str, Any]:
        """
        Get an overview of a module (directory) in the progenitor source.

        Args:
            module_name: Name of the module (e.g., "nodes", "tools", "state")

        Returns:
            Dict with module information
        """
        listing = self.list_directory(module_name)

        # Read init file if present
        init_content = None
        init_path = Path(module_name) / "__init__.py"
        try:
            init_file = self.root / init_path
            if init_file.exists():
                init_content = init_file.read_text(encoding="utf-8", errors="ignore")[:3000]
        except OSError:
            init_content = None

        # Extract exports from __init__.py
        exports = []
        if init_content:
            # Look for __all__ definition
            match = re.search(r"__all__\s*=\s*\[(.*?)\]", init_content, re.DOTALL)
            if match:
                exports = re.findall(r'"([^"]+)"|\'([^\']+)\'', match.group(1))
                exports = [e[0] or e[1] for e in exports]

        return {
            "module": module_name,
            "files": [e.name for e in listing.entries if not e.is_directory],
            "subdirectories": [e.name for e in listing.entries if e.is_directory],
            "file_count": listing.total_files,
            "exports": exports,
            "file_descriptions": {
                e.name: e.description
                for e in listing.entries
                if not e.is_directory and e.description
            },
        }


# ============================================================================
# Tool Functions for Integration
# ============================================================================


async def self_reflect_list_modules() -> dict[str, Any]:
    """List all modules in the Progenitor source code."""
    reflection = SelfReflection()
    modules = reflection.list_modules()

    return {
        "modules": [m.model_dump() for m in modules],
        "total": len(modules),
        "root": str(reflection.root),
    }


async def self_reflect_explore_directory(directory: str = "") -> dict[str, Any]:
    """Explore a directory in the Progenitor source code."""
    reflection = SelfReflection()
    listing = reflection.list_directory(directory)

    return listing.model_dump()


async def self_reflect_read_file(
    file_path: str,
    max_lines: int | None = None,
) -> dict[str, Any]:
    """Read a file from the Progenitor source code."""
    reflection = SelfReflection()
    content = reflection.read_file(file_path, max_lines)

    return content.model_dump()


async def self_reflect_search(
    query: str,
    directory: str = "",
    is_regex: bool = False,
    max_results: int = 20,
) -> dict[str, Any]:
    """Search for code patterns in the Progenitor source."""
    reflection = SelfReflection()
    results = reflection.search_code(
        query=query,
        directory=directory,
        is_regex=is_regex,
        max_results=max_results,
    )

    return results.model_dump()


async def self_reflect_find_relevant(
    topic: str,
    max_files: int = 10,
) -> dict[str, Any]:
    """Find files relevant to a topic in the Progenitor source."""
    reflection = SelfReflection()
    files = reflection.find_relevant_files(topic, max_files)

    return {
        "topic": topic,
        "relevant_files": [f.model_dump() for f in files],
        "count": len(files),
    }


async def self_reflect_module_overview(module_name: str) -> dict[str, Any]:
    """Get an overview of a module in the Progenitor source."""
    reflection = SelfReflection()
    return reflection.get_module_overview(module_name)


# ============================================================================
# Tool Specification for Registration
# ============================================================================

SELF_REFLECT_TOOL_SPEC = build_tool_spec(
    name="self_reflect",
    description="""Introspect Progenitor's own source code to understand what it does.

Use this tool to discover how Progenitor works by:
- Listing modules and files in the source tree
- Reading specific source files
- Searching for code patterns
- Finding files relevant to a topic

This is a READ-ONLY tool - it cannot modify any files.

Operations available via the 'operation' parameter:
- "list_modules": List all top-level modules
- "explore": List contents of a directory
- "read": Read a file's content
- "search": Search for code patterns
- "find_relevant": Find files related to a topic
- "module_overview": Get overview of a specific module

Examples:
- To understand how user input is handled: use operation="find_relevant" with topic="user input"
- To inspect decision handling: use operation="read" with file_path="nodes/chat.py" or file_path="routing.py"
- To search for scheduling code: use operation="search" with query="schedule"
""",
    args_model=SelfReflectArgs,
)


async def execute_self_reflect(
    operation: str,
    directory: str = "",
    file_path: str = "",
    query: str = "",
    topic: str = "",
    module_name: str = "",
    is_regex: bool = False,
    max_lines: int | None = None,
    max_results: int = 10,
) -> dict[str, Any]:
    """
    Execute a self-reflection operation.

    This is the main entry point for the self_reflect tool.
    """
    if operation == "list_modules":
        return await self_reflect_list_modules()

    elif operation == "explore":
        return await self_reflect_explore_directory(directory)

    elif operation == "read":
        if not file_path:
            raise ValueError("file_path required for 'read' operation")
        return await self_reflect_read_file(file_path, max_lines)

    elif operation == "search":
        if not query:
            raise ValueError("query required for 'search' operation")
        return await self_reflect_search(query, directory, is_regex, max_results)

    elif operation == "find_relevant":
        if not topic:
            raise ValueError("topic required for 'find_relevant' operation")
        return await self_reflect_find_relevant(topic, max_results)

    elif operation == "module_overview":
        if not module_name:
            raise ValueError("module_name required for 'module_overview' operation")
        return await self_reflect_module_overview(module_name)

    else:
        raise ValueError(f"Unknown operation: {operation}")


class SelfReflectModuleTool(BaseSystemTool):
    """Module-level wrapper for self_reflect operations (read-only)."""

    @property
    def name(self) -> str:
        return "self_reflect_module"

    @property
    def description(self) -> str:
        return "Read-only self-reflection helpers (module listing/search/read)"

    @property
    def keywords(self) -> list[str]:
        return []

    async def execute(self, context: ToolContext) -> ToolResult:
        reflection = SelfReflection()
        modules = reflection.list_modules()
        return ToolResult.ok(
            {
                "root_path": str(reflection.root),
                "modules": [m.model_dump() for m in modules],
                "tip": "Use the 'self_reflect' system tool for the common entry point; this is a module-level wrapper.",
            }
        )
