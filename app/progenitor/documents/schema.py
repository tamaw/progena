"""Embed document schemas into composed contracts without losing local references."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.progenitor.documents.types import DocumentType


# Root keywords that only make sense at a schema's own root. ``$id`` would
# re-base ``$ref`` resolution, so it is dropped; definitions are hoisted to the
# composed port schema's root so internal ``#/$defs/...`` refs keep resolving.
_SCHEMA_ROOT_ONLY_KEYS = ("$schema", "$id")


_SCHEMA_DEFINITION_KEYS = ("$defs", "definitions")


def typed_document_schema(
    document_type: DocumentType, document_schema: Mapping[str, Any] | None
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Return (embeddable document schema titled with the type label, root definitions).

    The title makes the port nominally typed: two document types sharing one
    schema file still only connect to ports of their own type.
    """

    embedded: dict[str, Any] = (
        copy.deepcopy(dict(document_schema)) if document_schema else {"type": "object"}
    )
    for key in _SCHEMA_ROOT_ONLY_KEYS:
        embedded.pop(key, None)
    definitions = {
        key: embedded.pop(key)
        for key in _SCHEMA_DEFINITION_KEYS
        if isinstance(embedded.get(key), dict)
    }
    embedded["title"] = document_type.label
    return embedded, definitions
