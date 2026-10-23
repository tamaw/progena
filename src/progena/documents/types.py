"""Document type registry: which ``store`` keys form which kind of document.

A document type maps a key (exact) or a key prefix (collection) in the
``progena.state`` / ``backend.state`` namespaces to a label and an optional
JSON schema under ``data/schemas/``. The registry itself lives in Postgres as
``document_type:<id>`` documents, so new kinds register as data rather than
code; ``BUILTIN_DOCUMENT_TYPES`` is only the seed written at startup.

Every create/update/delete of a typed document becomes a document event that a
workflow event node can subscribe to by type id, with ports derived from the
type's schema (see ``src.progena.workflows``).
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.progena.documents.catalog import load_schema
from src.progena.documents.kinds import (
    ACP_TASK_KEY_PREFIX,
    AGENT_DOCUMENT_KEY_PREFIX,
    ALERT_KEY_PREFIX,
    ARTIFACT_DOCUMENT_KEY_PREFIX,
    CANVAS_DATA_KEY_PREFIX,
    CANVAS_DOCUMENT_KEY_PREFIX,
    CANVAS_STATUS_KEY_PREFIX,
    DOCUMENT_TYPE_KEY_PREFIX,
    INBOX_MESSAGE_KEY_PREFIX,
    INSTRUCTION_DOCUMENT_KEY_PREFIX,
    KANBAN_CARD_KEY_PREFIX,
    KANBAN_TASK_CONTENT_KEY_PREFIX,
    KNOWLEDGE_PAGE_KEY_PREFIX,
    MEMORY_DOCUMENT_KEY_PREFIX,
    POLICY_KEY_PREFIX,
    REPO_KEY_PREFIX,
    ROUTINE_GRAPH_KEY_PREFIX,
    SESSION_KEY_PREFIX,
    SKILL_DOCUMENT_KEY_PREFIX,
    WORKFLOW_DEFINITION_KEY_PREFIX,
    WORKFLOW_RUN_KEY_PREFIX,
)

logger = logging.getLogger(__name__)

DocumentOperation = Literal["created", "updated", "deleted"]
DOCUMENT_OPERATIONS: tuple[DocumentOperation, ...] = ("created", "updated", "deleted")


class DocumentType(BaseModel):
    """One registered document type: exactly one of ``key_prefix`` or ``key``."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    label: str = Field(min_length=1)
    key_prefix: str | None = None
    key: str | None = None
    schema_name: str | None = Field(default=None, alias="schema")

    @model_validator(mode="after")
    def _exactly_one_selector(self) -> DocumentType:
        if (self.key_prefix is None) == (self.key is None):
            raise ValueError(f"document type {self.id!r} needs exactly one of key_prefix or key")
        if self.key_prefix is not None and not self.key_prefix:
            raise ValueError(f"document type {self.id!r} has an empty key_prefix")
        return self

    @property
    def collection(self) -> bool:
        """Whether this type is a prefix-keyed collection of documents."""

        return self.key_prefix is not None

    def matches(self, key: str) -> bool:
        if self.key is not None:
            return key == self.key
        return self.key_prefix is not None and key.startswith(self.key_prefix)

    def document_id(self, key: str) -> str:
        """Return the id part of ``key`` (the key itself for single documents)."""

        if self.key_prefix is not None and key.startswith(self.key_prefix):
            return key[len(self.key_prefix) :]
        return key

    def document_key(self, document_id: str) -> str:
        """Return the store key for one document id of this type."""

        if self.key is not None:
            return self.key
        return f"{self.key_prefix}{document_id}"

    def load_schema(self) -> dict[str, Any] | None:
        """Load this type's document schema from ``data/schemas/``, if any."""

        if self.schema_name is None:
            return None
        return load_schema(self.schema_name)

    @property
    def writable_fields(self) -> tuple[str, ...]:
        """Top-level schema properties workflow writes may change."""

        schema = self.load_schema()
        properties = schema.get("properties") if isinstance(schema, Mapping) else None
        if not isinstance(properties, Mapping):
            return ()
        return tuple(
            name
            for name, definition in properties.items()
            if isinstance(name, str)
            and isinstance(definition, Mapping)
            and definition.get("readOnly") is not True
        )

    def validate_write(self, fields: Mapping[str, Any]) -> str | None:
        """Check a partial write against writable schema properties."""

        schema = self.load_schema()
        writable = self.writable_fields
        unknown = sorted(set(fields) - set(writable))
        if unknown:
            return f"Fields are not writable: {', '.join(unknown)}"
        if schema is None:
            return None
        from src.progena.llm.structured import validate_against_schema

        properties = schema.get("properties", {})
        partial = {
            "type": "object",
            "properties": {field: properties[field] for field in writable},
            "additionalProperties": False,
            **{name: schema[name] for name in ("$defs", "definitions") if name in schema},
        }
        return validate_against_schema(dict(fields), partial)

    def payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True)


def _prefix_type(type_id: str, label: str, prefix: str, schema: str | None) -> DocumentType:
    return DocumentType(id=type_id, label=label, key_prefix=prefix, schema=schema)


def _single_type(type_id: str, label: str, key: str, schema: str | None) -> DocumentType:
    return DocumentType(id=type_id, label=label, key=key, schema=schema)


BUILTIN_DOCUMENT_TYPES: tuple[DocumentType, ...] = (
    _prefix_type("kanban_card", "Kanban Card", KANBAN_CARD_KEY_PREFIX, "task"),
    _prefix_type(
        "kanban_task", "Kanban Task Body", KANBAN_TASK_CONTENT_KEY_PREFIX, "content_document"
    ),
    _prefix_type("inbox_message", "Inbox Message", INBOX_MESSAGE_KEY_PREFIX, "inbox_message"),
    _prefix_type("workflow", "Workflow", WORKFLOW_DEFINITION_KEY_PREFIX, "definition.workflow"),
    _prefix_type("workflow_run", "Workflow Run", WORKFLOW_RUN_KEY_PREFIX, "workflow_run"),
    _prefix_type("acp_task", "ACP Task", ACP_TASK_KEY_PREFIX, "acp_task"),
    _prefix_type("routine", "Routine", ROUTINE_GRAPH_KEY_PREFIX, "graph.routine"),
    _prefix_type("policy", "Policy", POLICY_KEY_PREFIX, "policy"),
    _prefix_type("agent", "Agent", AGENT_DOCUMENT_KEY_PREFIX, "content_document"),
    _prefix_type("skill", "Skill", SKILL_DOCUMENT_KEY_PREFIX, "content_document"),
    _prefix_type("canvas", "Canvas", CANVAS_DOCUMENT_KEY_PREFIX, "content_document"),
    _prefix_type("canvas_data", "Canvas Data", CANVAS_DATA_KEY_PREFIX, None),
    _prefix_type("canvas_status", "Canvas Status", CANVAS_STATUS_KEY_PREFIX, None),
    _prefix_type("artifact", "Artifact", ARTIFACT_DOCUMENT_KEY_PREFIX, "content_document"),
    _prefix_type("memory", "Memory", MEMORY_DOCUMENT_KEY_PREFIX, "content_document"),
    _prefix_type("instruction", "Instruction", INSTRUCTION_DOCUMENT_KEY_PREFIX, "content_document"),
    _single_type("preferences", "Preferences", "preferences", "preferences"),
    _single_type("schedule", "Schedule", "schedule", None),
    _single_type("mcp", "MCP Servers", "mcp", None),
    _single_type("plugins", "Plugins", "plugins", None),
    _single_type("marketplaces", "Marketplaces", "marketplaces", None),
    _single_type("knowledge", "Knowledge", "knowledge", None),
    _prefix_type("repo", "Repository", REPO_KEY_PREFIX, "repo_entry"),
    _prefix_type("alert", "Alert", ALERT_KEY_PREFIX, "alert"),
    _prefix_type("knowledge_page", "Knowledge Page", KNOWLEDGE_PAGE_KEY_PREFIX, "content_document"),
    _single_type(
        "knowledge_index",
        "Knowledge Search Index",
        "knowledge_index",
        "knowledge_index",
    ),
    _prefix_type("session", "Session", SESSION_KEY_PREFIX, "session"),
    _single_type("kanban_board", "Kanban Board", "kanban_board", None),
)


def document_type_key(type_id: str) -> str:
    """Return the registry document key for one document type id."""

    return f"{DOCUMENT_TYPE_KEY_PREFIX}{type_id}"


def parse_document_types(payloads: Iterable[Any]) -> dict[str, DocumentType]:
    """Parse registry payloads into a by-id map, skipping malformed rows."""

    types: dict[str, DocumentType] = {}
    for payload in payloads:
        try:
            document_type = DocumentType.model_validate(payload)
        except Exception:
            logger.warning("Skipping malformed document type row: %r", payload, exc_info=True)
            continue
        types[document_type.id] = document_type
    return types


def resolve_document_type(
    key: str,
    types: Iterable[DocumentType] | Mapping[str, DocumentType],
) -> DocumentType | None:
    """Return the type owning ``key``: an exact key wins, else the longest prefix."""

    candidates = types.values() if isinstance(types, Mapping) else types
    best: DocumentType | None = None
    for document_type in candidates:
        if not document_type.matches(key):
            continue
        if document_type.key is not None:
            return document_type
        if best is None or len(document_type.key_prefix or "") > len(best.key_prefix or ""):
            best = document_type
    return best


def builtin_document_type_rows() -> list[tuple[str, dict[str, Any]]]:
    """Return ``(key, payload)`` seed rows for every built-in document type."""

    return [(document_type_key(item.id), item.payload()) for item in BUILTIN_DOCUMENT_TYPES]
