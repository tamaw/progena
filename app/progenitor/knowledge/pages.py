"""Pure validation and rendering rules for markdown knowledge pages."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Literal

import yaml

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


def split_front_matter(content: str) -> tuple[dict[str, Any] | None, str]:
    if not content.startswith("---\n"):
        return None, content
    match = re.search(r"^---\s*$", content[4:], flags=re.MULTILINE)
    if match is None:
        raise ValueError("unclosed YAML front matter")
    end = 4 + match.start()
    body_start = 4 + match.end()
    if content[body_start : body_start + 1] == "\n":
        body_start += 1
    try:
        parsed = yaml.safe_load(content[4:end])
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid YAML front matter: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("YAML front matter must be a mapping")
    return parsed, content[body_start:]


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
    search_status: str = "",
) -> str:
    lines: list[str] = []
    if is_base_root:
        lines.extend(["---", 'okf_version: "0.2"', "---", ""])
    lines.append(f"# {title}")
    if is_base_root and search_status:
        lines.extend(["", f"Search index: {search_status}"])
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


def render_directory_index(
    *, title: str, directory: str, live_pages: list[tuple[str, str]], search_status: str = ""
) -> str:
    """Render ``directory``'s index from ``(path, content)`` of a base's live pages."""
    dir_prefix = f"{directory}/" if directory else ""
    pages: list[tuple[str, dict[str, Any]]] = []
    subdirs: set[str] = set()
    for path, content in live_pages:
        if not path.startswith(dir_prefix):
            continue
        rest = path[len(dir_prefix) :]
        if "/" in rest:
            subdirs.add(rest.split("/", 1)[0])
            continue
        try:
            if classify_page_path(path) != "concept":
                continue
            front_matter, _ = split_front_matter(content)
        except ValueError:
            continue
        if front_matter:
            pages.append((rest, front_matter))
    return render_index(
        title=title,
        dir_path=directory,
        pages=pages,
        subdirs=sorted(subdirs),
        is_base_root=not directory,
        search_status=search_status,
    )
