"""
Memory Tool - Store and manage persistent memories.
"""

import json
import logging
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..env_utils import env_int
from ..llm.prompts import PromptTemplates
from ..runtime.time_utils import format_timestamp, utcnow_isoformat
from .protocol import BaseSystemTool, ToolContext, ToolResult

logger = logging.getLogger(__name__)
_prompts = PromptTemplates()


@dataclass
class MemoryEntry:
    """In-memory representation of a stored memory entry."""

    id: str
    created_at: str
    summary: str
    details: str
    tags: list[str]
    confidence: float
    raw_input: str
    source: str = "user"
    run_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "created_at": self.created_at,
            "summary": self.summary,
            "details": self.details,
            "tags": self.tags,
            "confidence": self.confidence,
            "raw_input": self.raw_input,
            "source": self.source,
            "run_id": self.run_id,
        }


_CLEAR_RE = re.compile(r"\b(clear|wipe|reset)\s+(all\s+)?memories?\b|\bforget\s+all\b")
_LIST_RE = re.compile(r"\b(list|show)\s+memories?\b|\bmemories\s+list\b")
_SEARCH_RE = re.compile(r"\b(search|find|lookup)\s+memories?\b\s*(.*)$")
_DELETE_RE = re.compile(r"\b(delete|remove|forget)\s+(?:memory|memories)?\s*(.*)$")
_STORE_RE = re.compile(r"\b(remember|save|store)\b")


class MemoryTool(BaseSystemTool):
    """Store and manage persistent memories."""

    @property
    def name(self) -> str:
        return "memory"

    @property
    def description(self) -> str:
        return "Store and manage persistent memories"

    @property
    def keywords(self) -> list[str]:
        return [
            "remember",
            "save memory",
            "store memory",
            "list memories",
            "show memories",
            "delete memory",
            "remove memory",
            "forget memory",
            "clear memory",
            "clear memories",
            "search memories",
            "find memory",
        ]

    async def execute(self, context: ToolContext) -> ToolResult:
        user_input = (context.user_input or "").strip()
        if not user_input:
            return ToolResult.fail("No input provided.")

        action, query = _parse_action(user_input)
        memory_dir = _get_memory_dir()
        memory_dir.mkdir(parents=True, exist_ok=True)

        if action == "store":
            if context.llm_client is None:
                return ToolResult.fail("LLM client not available for memory extraction.")
            return await _store_memory(context, user_input, memory_dir)

        if action == "list":
            limit = env_int("PROGENITOR_MEMORY_LIST_LIMIT", 10)
            entries = _load_entries(memory_dir)
            entries_sorted = _sort_entries(entries)
            return ToolResult.ok({
                "action": "list",
                "entries": _summarize_entries(entries_sorted[:limit]),
                "total_count": len(entries),
                "limit": limit,
            })

        if action == "search":
            if not query:
                return ToolResult.fail("Please provide a search query for memory lookup.")
            entries = _load_entries(memory_dir)
            matches = _filter_entries(entries, query)
            return ToolResult.ok({
                "action": "search",
                "query": query,
                "entries": _summarize_entries(_sort_entries(matches)),
            })

        if action == "delete":
            if not query:
                return ToolResult.fail("Please provide a memory ID or search query to delete.")
            deleted = _delete_entries(memory_dir, query)
            return ToolResult.ok({
                "action": "delete",
                "query": query,
                "deleted_count": deleted,
            })

        if action == "clear":
            cleared = _clear_entries(memory_dir)
            return ToolResult.ok({
                "action": "clear",
                "cleared_count": cleared,
            })

        return ToolResult.fail(
            "Unrecognized memory action. Try 'remember', 'list memories', 'search memories', or 'clear memory'."
        )


def get_relevant_memories(
    query: str,
    limit: int = 5,
    min_confidence: float = 0.0,
) -> list[dict[str, Any]]:
    """Return relevant memory entries for a query."""
    if not query.strip():
        return []

    memory_dir = _get_memory_dir()
    if not memory_dir.exists():
        return []

    entries = _load_entries(memory_dir)
    matches = _filter_entries(entries, query)
    filtered = [
        entry for entry in matches
        if float(entry.get("confidence", 0.0) or 0.0) >= min_confidence
    ]
    return _sort_entries(filtered)[:max(1, limit)]


def format_memory_context(entries: list[dict[str, Any]], max_chars: int = 1200) -> str:
    """Format memory entries for injection into LLM context."""
    if not entries:
        return ""

    lines = ["Relevant memories:"]
    for entry in entries:
        summary = str(entry.get("summary", "")).strip()
        details = str(entry.get("details", "")).strip()
        tags = entry.get("tags", [])
        tag_str = ", ".join([str(t) for t in tags]) if tags else ""
        created_at = entry.get("created_at", "")
        entry_id = entry.get("id", "")

        line = f"- {summary}"
        if details and details != summary:
            line += f" | {details}"
        if tag_str:
            line += f" | tags: {tag_str}"
        if created_at:
            line += f" | created: {created_at}"
        if entry_id:
            line += f" | id: {entry_id}"
        lines.append(line)

    output = "\n".join(lines).strip()
    if len(output) <= max_chars:
        return output
    return output[: max(0, max_chars - 3)] + "..."


def _parse_action(user_input: str) -> tuple[str, str | None]:
    text = user_input.lower().strip()

    if _CLEAR_RE.search(text):
        return "clear", None

    if _LIST_RE.search(text):
        return "list", None

    search_match = _SEARCH_RE.search(text)
    if search_match:
        query = search_match.group(2).strip() if search_match.group(2) else ""
        return "search", query or None

    delete_match = _DELETE_RE.search(text)
    if delete_match:
        query = delete_match.group(2).strip() if delete_match.group(2) else ""
        return "delete", query or None

    if _STORE_RE.search(text):
        return "store", None

    return "list", None


def _get_memory_dir() -> Path:
    project_root = Path(__file__).parent.parent.parent.parent
    env_dir = os.getenv("PROGENITOR_MEMORY_DIR")
    if env_dir:
        path = Path(env_dir)
        if not path.is_absolute():
            path = project_root / path
        return path

    return project_root / "data" / "memory"


async def _store_memory(context: ToolContext, user_input: str, memory_dir: Path) -> ToolResult:
    prompt = _prompts.get("memory.extract.md", user_input=user_input)
    system_prompt = _prompts.get("memory.extract_system.md")

    try:
        response = await context.llm_client.complete(
            prompt=prompt,
            system_prompt=system_prompt,
            temperature=0.2,
        )
    except Exception as e:
        logger.warning("Memory extraction failed: %s", e)
        response = None

    parsed: dict[str, Any] = {}
    if response:
        try:
            parsed = json.loads(response.strip())
        except json.JSONDecodeError:
            logger.warning("Memory extraction returned invalid JSON")

    if parsed.get("skip") is True:
        return ToolResult.ok({
            "action": "skip",
            "reason": parsed.get("reason", "No durable memory found."),
        })

    summary = _ensure_text(parsed.get("summary")) or _fallback_summary(user_input)
    details = _ensure_text(parsed.get("details")) or user_input
    tags = _ensure_tags(parsed.get("tags"))
    confidence = _ensure_confidence(parsed.get("confidence"))

    entry_id = uuid.uuid4().hex
    created_at = utcnow_isoformat()
    run_id = None
    if context.state and isinstance(context.state, dict):
        run_id = context.state.get("run_id")

    entry = MemoryEntry(
        id=entry_id,
        created_at=created_at,
        summary=summary,
        details=details,
        tags=tags,
        confidence=confidence,
        raw_input=user_input,
        run_id=run_id,
    )

    filename = f"{format_timestamp()}_{entry_id}.json"
    path = memory_dir / filename
    path.write_text(json.dumps(entry.to_dict(), indent=2, ensure_ascii=True))

    return ToolResult.ok({
        "action": "store",
        "entry": entry.to_dict(),
        "file": str(path),
    })


def _load_entries(memory_dir: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for path in memory_dir.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data["_path"] = str(path)
                entries.append(data)
        except Exception:
            continue
    return entries


def _sort_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def _key(entry: dict[str, Any]) -> str:
        created = str(entry.get("created_at", ""))
        return created

    return sorted(entries, key=_key, reverse=True)


def _summarize_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summary: list[dict[str, Any]] = []
    for entry in entries:
        summary.append({
            "id": entry.get("id"),
            "summary": entry.get("summary"),
            "created_at": entry.get("created_at"),
            "tags": entry.get("tags", []),
        })
    return summary


def _filter_entries(entries: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    needle = query.lower()
    matches: list[dict[str, Any]] = []
    for entry in entries:
        haystack = " ".join([
            str(entry.get("id", "")),
            str(entry.get("summary", "")),
            str(entry.get("details", "")),
            str(entry.get("raw_input", "")),
            " ".join([str(t) for t in entry.get("tags", [])]),
        ]).lower()
        if needle in haystack:
            matches.append(entry)
    return matches


def _delete_entries(memory_dir: Path, query: str) -> int:
    entries = _load_entries(memory_dir)
    matches = _filter_entries(entries, query)
    deleted = 0
    for entry in matches:
        path = entry.get("_path")
        if not path:
            continue
        try:
            Path(path).unlink(missing_ok=True)
            deleted += 1
        except Exception:
            continue
    return deleted


def _clear_entries(memory_dir: Path) -> int:
    deleted = 0
    for path in memory_dir.glob("*.json"):
        try:
            path.unlink(missing_ok=True)
            deleted += 1
        except Exception:
            continue
    return deleted


def _ensure_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    return ""


def _ensure_tags(value: Any) -> list[str]:
    if isinstance(value, list):
        tags = [str(item).strip() for item in value if str(item).strip()]
        return tags[:10]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _ensure_confidence(value: Any) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.5
    return max(0.0, min(1.0, confidence))


def _fallback_summary(user_input: str) -> str:
    text = user_input.strip()
    if len(text) <= 200:
        return text
    return text[:197] + "..."
