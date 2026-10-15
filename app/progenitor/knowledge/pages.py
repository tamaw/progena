"""Pure validation and rendering rules for markdown knowledge pages."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Literal

import yaml

from ..front_matter import split_front_matter

INDEX_FILE = "index.md"
LOG_FILE = "log.md"
INDEXER_FILE = "indexer.json"
KNOWLEDGE_MOUNT = "/knowledge/"
BASE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
SEGMENT_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")

PageKind = Literal["concept", "index", "log"]
_CONCEPT_ERROR = (
    "knowledge pages need YAML front matter with a non-empty 'type' (OKF v0.2), e.g.\n"
    "---\ntype: Concept\ntitle: ...\ndescription: ...\n---"
)
_PATH_ERROR = (
    "page paths must be lowercase kebab-case segments ending in .md (e.g. concepts/retry-policy.md)"
)


def knowledge_page_key(base_id: str, path: str) -> str:
    return f"knowledge:{base_id}/{path}"


def classify_page_path(path: str) -> PageKind:
    if path.startswith("/") or len(path) > 240 or not path or "\\" in path:
        raise ValueError(_PATH_ERROR)
    parts = path.split("/")
    if parts[-1] == LOG_FILE:
        if len(parts) != 1:
            raise ValueError("log.md is only allowed at the base root")
        return "log"
    if parts[-1] == INDEX_FILE:
        return "index"
    if any(not SEGMENT_RE.fullmatch(part) for part in parts[:-1]):
        raise ValueError(_PATH_ERROR)
    if not parts[-1].endswith(".md") or not SEGMENT_RE.fullmatch(parts[-1][:-3]):
        raise ValueError(_PATH_ERROR)
    return "concept"


def validate_concept_page(content: str) -> None:
    front_matter, _ = split_front_matter(content)
    if (
        not front_matter
        or not isinstance(front_matter.get("type"), str)
        or not front_matter["type"].strip()
    ):
        raise ValueError(_CONCEPT_ERROR)


def stamp_generated(previous: str | None, content: str, now_iso: str) -> str:
    _old_fm, old_body = split_front_matter(previous) if previous is not None else (None, None)
    front_matter, body = split_front_matter(content)
    if previous is not None and old_body == body:
        return content
    if front_matter is None:
        raise ValueError(_CONCEPT_ERROR)
    generated = front_matter.get("generated")
    if not isinstance(generated, dict):
        generated = {}
    front_matter["generated"] = {**generated, "at": now_iso}
    return (
        "---\n" + yaml.safe_dump(front_matter, sort_keys=False, allow_unicode=True) + "---\n" + body
    )


def check_log_append(previous: str | None, content: str) -> None:
    if previous is not None and not content.startswith(previous):
        raise ValueError(
            "log.md is append-only: keep the existing text unchanged and add new entries at the end"
        )


def render_index(
    *,
    title: str,
    dir_path: str,
    pages: list[tuple[str, dict[str, Any]]],
    subdirs: list[str],
    is_base_root: bool,
    search_index: Mapping[str, Any] | None = None,
) -> str:
    lines: list[str] = []
    if is_base_root:
        header: dict[str, Any] = {"okf_version": "0.2"}
        if search_index is not None:
            header["search_index"] = dict(search_index)
        dumped = yaml.safe_dump(header, sort_keys=False, allow_unicode=True)
        lines.extend(["---", *dumped.rstrip("\n").split("\n"), "---", ""])
    lines.append(f"# {title}")
    grouped: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for filename, front_matter in pages:
        grouped.setdefault(str(front_matter.get("type", "Concept")), []).append(
            (filename, front_matter)
        )
    for page_type in sorted(grouped):
        lines.extend(["", f"## {page_type}"])
        for filename, front_matter in sorted(
            grouped[page_type], key=lambda item: str(item[1].get("title") or item[0][:-3]).lower()
        ):
            label = str(front_matter.get("title") or filename[:-3])
            description = str(front_matter.get("description") or "").strip()
            suffix = f" — {description}" if description else ""
            lines.append(f"- [{label}]({filename}){suffix}")
    if subdirs:
        lines.extend(["", "## Directories"])
        lines.extend(f"- [{name}/]({name}/index.md)" for name in sorted(subdirs))
    if is_base_root:
        lines.extend(["", "## Reserved", f"- [log.md]({LOG_FILE})"])
    return "\n".join(lines) + "\n"


def render_root_index(
    bases: list[dict[str, Any]], search_statuses: Mapping[str, str] | None = None
) -> str:
    statuses = search_statuses or {}
    lines = ["# Knowledge", ""]
    for base in sorted(bases, key=lambda item: str(item.get("name", "")).lower()):
        description = str(base.get("description") or "").strip()
        suffix = f" — {description}" if description else ""
        status = statuses.get(str(base.get("id")))
        if status:
            suffix += f" · search: {status}"
        lines.append(f"- [{base.get('name', base.get('id'))}]({base.get('id')}/index.md){suffix}")
    lines.extend(["", f"Search indexer status: [{INDEXER_FILE}]({INDEXER_FILE})"])
    return "\n".join(lines) + "\n"


def is_deprecated(front_matter: Mapping[str, Any] | None) -> bool:
    """Whether a page's front matter retires it (``status: deprecated``).

    Deprecated pages are hidden from generated indexes and the backend page list,
    and rank last in search (``index._DEPRECATED_SCORE_FACTOR``). Deprecation stands
    in for deletion while agents have no delete tool (deepagents < 0.7).
    deepagents 0.7 adds a ``delete`` filesystem tool backed by ``adelete``
    (``KnowledgePageBackend.adelete``); once we upgrade, let agents delete pages
    there (revisions keep the history) and remove this rule, its callers, and the
    ``deprecated`` column/ranking in ``index.py``.
    """
    return (
        bool(front_matter) and str(front_matter.get("status", "")).strip().lower() == "deprecated"
    )


def render_directory_index(
    *,
    title: str,
    directory: str,
    live_pages: list[tuple[str, str]],
    search_index: Mapping[str, Any] | None = None,
) -> str:
    """Render ``directory``'s index from ``(path, content)`` of a base's live pages.

    Deprecated pages are left out (they stay readable by path), and so are
    subdirectories that hold nothing else.
    """
    dir_prefix = f"{directory}/" if directory else ""
    pages: list[tuple[str, dict[str, Any]]] = []
    subdirs: set[str] = set()
    for path, content in live_pages:
        if not path.startswith(dir_prefix):
            continue
        try:
            if classify_page_path(path) != "concept":
                continue
            front_matter, _ = split_front_matter(content)
        except ValueError:
            continue
        if not front_matter or is_deprecated(front_matter):
            continue
        rest = path[len(dir_prefix) :]
        if "/" in rest:
            subdirs.add(rest.split("/", 1)[0])
        else:
            pages.append((rest, front_matter))
    return render_index(
        title=title,
        dir_path=directory,
        pages=pages,
        subdirs=sorted(subdirs),
        is_base_root=not directory,
        search_index=search_index,
    )
