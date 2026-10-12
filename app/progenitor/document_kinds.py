"""Canonical registry of ``store`` table document key prefixes.

Every Postgres-backed document kind (routines, agents, skills, memories,
prompts, kanban cards, inbox messages, workflows, workflow runs, ...) is
identified by a fixed prefix on its ``key`` column, e.g. ``"routine:"`` for
``routine:<id>``. Those prefixes used to be declared independently in each
owning module (``content_documents.py``, ``workflows.py``,
``routines/store.py``, ``state/manager.py``, ``inbox_routes.py``,
``ui_routes.py``) with no shared source of truth -- ``INBOX_MESSAGE_KEY_PREFIX``
had drifted into two separate declarations (``state/manager.py`` and
``app/backend/inbox_routes.py``) before this module existed.

This module is the single source of truth: every owning module now imports
its prefix constant from here instead of declaring its own literal, and
``DOCUMENT_KINDS`` is the canonical, user-facing list consumed by anything
that needs to present "pick a document kind" (e.g. a workflow event
trigger's ``key_prefix`` filter).

Pure constants module -- no imports from the rest of the codebase, so every
other module can safely depend on it without risking an import cycle.
"""

from __future__ import annotations

from dataclasses import dataclass

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
INBOX_MESSAGE_KEY_PREFIX = "inbox_message:"
WORKFLOW_DEFINITION_KEY_PREFIX = "workflow:"
WORKFLOW_RUN_KEY_PREFIX = "workflow_run:"
KNOWLEDGE_PAGE_KEY_PREFIX = "knowledge:"

# Internal-plumbing prefixes: real ``store`` key prefixes, still centralized
# here to avoid re-drift, but deliberately left out of ``DOCUMENT_KINDS``
# since they are not document kinds a user would pick.
WORKFLOW_QUEUE_KEY_PREFIX = "workflow_queue:"
WORKFLOW_INVOKE_REQUEST_KEY_PREFIX = "workflow_invoke_request:"
AGENT_SELF_EDIT_TOUCH_KEY_PREFIX = "agent-touch:"


@dataclass(frozen=True)
class DocumentKind:
    """One user-facing document kind: a display label plus its key prefix."""

    label: str
    key_prefix: str


DOCUMENT_KINDS: tuple[DocumentKind, ...] = (
    DocumentKind("Policy", POLICY_KEY_PREFIX),
    DocumentKind("Routine", ROUTINE_GRAPH_KEY_PREFIX),
    DocumentKind("Agent", AGENT_DOCUMENT_KEY_PREFIX),
    DocumentKind("Skill", SKILL_DOCUMENT_KEY_PREFIX),
    DocumentKind("Memory", MEMORY_DOCUMENT_KEY_PREFIX),
    DocumentKind("Prompt", PROMPT_DOCUMENT_KEY_PREFIX),
    DocumentKind("Kanban Card", KANBAN_CARD_KEY_PREFIX),
    DocumentKind("Inbox Message", INBOX_MESSAGE_KEY_PREFIX),
    DocumentKind("Workflow", WORKFLOW_DEFINITION_KEY_PREFIX),
    DocumentKind("Workflow Run", WORKFLOW_RUN_KEY_PREFIX),
    DocumentKind("Knowledge Page", KNOWLEDGE_PAGE_KEY_PREFIX),
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
