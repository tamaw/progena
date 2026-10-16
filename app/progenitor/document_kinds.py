"""Canonical ``store`` table document key prefixes.

Every Postgres-backed document kind (routines, agents, skills, memories,
prompts, kanban cards, inbox messages, workflows, workflow runs, ...) is
identified by a fixed prefix on its ``key`` column, e.g. ``"routine:"`` for
``routine:<id>``. Owning modules import their prefix constant from here instead
of declaring their own literal, so the prefixes cannot drift apart.

Which prefixes form a user-facing *document type* (label + schema) is data, not
code: see ``document_types`` and its ``document_type:<id>`` rows in Postgres.

Pure constants module -- no imports from the rest of the codebase, so every
other module can safely depend on it without risking an import cycle.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# Document kinds a user can reasonably pick as a workflow trigger filter or
# otherwise reason about as "a kind of document". Excludes internal-plumbing
# prefixes (workflow queues/invoke-requests, the agent self-edit touch
# marker) that are implementation details, not document kinds.
POLICY_KEY_PREFIX = "policy:"
ROUTINE_GRAPH_KEY_PREFIX = "routine:"
AGENT_DOCUMENT_KEY_PREFIX = "agent:"
SKILL_DOCUMENT_KEY_PREFIX = "skill:"
MEMORY_DOCUMENT_KEY_PREFIX = "memory:"
PROMPT_DOCUMENT_KEY_PREFIX = "prompt:"
KANBAN_CARD_KEY_PREFIX = "kanban_card:"
KANBAN_TASK_CONTENT_KEY_PREFIX = "kanban_task_content:"
INBOX_MESSAGE_KEY_PREFIX = "inbox_message:"
WORKFLOW_DEFINITION_KEY_PREFIX = "workflow:"
WORKFLOW_RUN_KEY_PREFIX = "workflow_run:"
DOCUMENT_TYPE_KEY_PREFIX = "document_type:"
SESSION_KEY_PREFIX = "session:"
REPO_KEY_PREFIX = "repo:"
ALERT_KEY_PREFIX = "alert:"
KNOWLEDGE_PAGE_KEY_PREFIX = "knowledge:"

# Internal-plumbing prefixes: real ``store`` key prefixes, centralized here to
# avoid re-drift, but not registered as document types.
WORKFLOW_QUEUE_KEY_PREFIX = "workflow_queue:"
WORKFLOW_INVOKE_REQUEST_KEY_PREFIX = "workflow_invoke_request:"
WORKFLOW_APPROVAL_REQUEST_KEY_PREFIX = "workflow_approval_request:"
AGENT_SELF_EDIT_TOUCH_KEY_PREFIX = "agent-touch:"


@dataclass(frozen=True)
class SearchSpec:
    """How one document kind participates in global search.

    ``route`` is an in-app path template; ``{id}`` is replaced with the
    document key minus the kind prefix.  For ``source="front_matter"`` the
    payload is expected to be ``{"content": "..."}`` containing OKF
    markdown with YAML front matter.
    """

    section: str
    route: str
    fields: tuple[str, ...]
    source: Literal["json", "front_matter"] = "json"
    strategy: Literal["text", "knowledge"] = "text"
    title_field: str = "title"
    key_suffix: str | None = None


@dataclass(frozen=True)
class DocumentKind:
    """One user-facing document kind: a display label, its key prefix, and how
    the global search covers it (``None`` = not searchable). ``search`` has no
    default so every new kind has to decide."""

    label: str
    key_prefix: str
    search: SearchSpec | None


DOCUMENT_KINDS: tuple[DocumentKind, ...] = (
    DocumentKind("Policy", POLICY_KEY_PREFIX, search=None),
    DocumentKind(
        "Routine",
        ROUTINE_GRAPH_KEY_PREFIX,
        search=SearchSpec(
            section="Routines",
            route="/routines/{id}",
            fields=("name", "description"),
            title_field="name",
        ),
    ),
    DocumentKind(
        "Agent",
        AGENT_DOCUMENT_KEY_PREFIX,
        search=SearchSpec(
            section="Agents",
            route="/agents/{id}/details",
            fields=("name", "description"),
            source="front_matter",
            title_field="name",
            key_suffix="/SYSTEM.md",
        ),
    ),
    DocumentKind("Skill", SKILL_DOCUMENT_KEY_PREFIX, search=None),
    DocumentKind("Memory", MEMORY_DOCUMENT_KEY_PREFIX, search=None),
    DocumentKind(
        "Prompt",
        PROMPT_DOCUMENT_KEY_PREFIX,
        search=SearchSpec(
            section="Prompts",
            route="/prompts?path={id}",
            fields=("content",),
            title_field="",
        ),
    ),
    DocumentKind(
        "Kanban Card",
        KANBAN_CARD_KEY_PREFIX,
        search=SearchSpec(
            section="Tasks",
            route="/tasks/{id}",
            fields=("title", "summary", "description"),
            title_field="title",
        ),
    ),
    DocumentKind(
        "Inbox Message",
        INBOX_MESSAGE_KEY_PREFIX,
        search=SearchSpec(
            section="Inbox",
            route="/inbox/{id}",
            fields=("title", "body"),
            title_field="title",
        ),
    ),
    DocumentKind(
        "Workflow",
        WORKFLOW_DEFINITION_KEY_PREFIX,
        search=SearchSpec(
            section="Workflows",
            route="/workflows/{id}",
            fields=("title", "description"),
            title_field="title",
        ),
    ),
    DocumentKind("Workflow Run", WORKFLOW_RUN_KEY_PREFIX, search=None),
    DocumentKind("Repository", REPO_KEY_PREFIX, search=None),
    DocumentKind("Alert", ALERT_KEY_PREFIX, search=None),
    DocumentKind(
        "Knowledge Page",
        KNOWLEDGE_PAGE_KEY_PREFIX,
        search=SearchSpec(
            section="Knowledge",
            route="/knowledge/{id}",
            fields=("title",),
            source="front_matter",
            strategy="knowledge",
            title_field="title",
        ),
    ),
)


def key_prefix_for(key: str) -> str | None:
    """Return the known document-kind key prefix that ``key`` starts with.

    Returns ``None`` for keys belonging to no registered document kind
    (internal plumbing rows, unrecognized prefixes, etc).
    """

    for kind in DOCUMENT_KINDS:
        if key.startswith(kind.key_prefix):
            return kind.key_prefix
    return None
