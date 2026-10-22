"""Documents capabilities."""

from __future__ import annotations

import json
from typing import Any

import yaml

from src.progenitor.documents.markdown import _split_frontmatter
from src.progenitor.documents.values import JsonDoc


def validate_kanban_structure(content: str) -> JsonDoc:
    """Parse + validate a ``tasks/board.json`` (board structure) payload.

    The structure document carries board-level data only — columns and the
    ordered ``card_order`` list. Individual cards live in their own
    ``tasks/<id>.md`` files, so this guards only the structure shape.
    """
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("top level must be a JSON object")
    columns = data.get("columns", [])
    if not isinstance(columns, list):
        raise ValueError("'columns' must be a list")
    order = data.get("card_order", [])
    if not isinstance(order, list):
        raise ValueError("'card_order' must be a list")
    if "cards" in data:
        raise ValueError("board.json holds structure only; edit tasks/<id>.md for cards")
    return data


def card_to_markdown(card: dict[str, Any]) -> str:
    """Render one card as YAML frontmatter (fields) + markdown body (description)."""
    fields = {k: v for k, v in card.items() if k != "description"}
    body = str(card.get("description") or "").strip("\n")
    frontmatter = yaml.safe_dump(
        fields, sort_keys=True, default_flow_style=False, allow_unicode=True
    ).strip()
    if body:
        return f"---\n{frontmatter}\n---\n\n{body}\n"
    return f"---\n{frontmatter}\n---\n"


def markdown_to_card(content: str, card_id: str, project_id: str | None = None) -> dict[str, Any]:
    """Parse a ``tasks/<id>.md`` or ``tasks/<project>/<id>.md`` file into a card dict.

    The file's location is authoritative, same as e.g. the registry's
    ``name``-as-key convention: the filename sets ``id`` and the enclosing
    directory (if any) sets ``project_id``, overriding whatever the frontmatter
    says. The markdown body becomes the card ``description``. Validation is
    light (a title is required) since the backend re-normalizes the assembled
    board on read.
    """
    fields, body = _split_frontmatter(content)
    card: dict[str, Any] = dict(fields)
    card["id"] = card_id
    if project_id is not None:
        card["project_id"] = project_id
    else:
        card.pop("project_id", None)
    body = body.strip("\n")
    if body:
        card["description"] = body
    if not str(card.get("title") or "").strip():
        raise ValueError("card is missing a 'title'")
    return card
