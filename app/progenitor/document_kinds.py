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

# Internal-plumbing prefixes: real ``store`` key prefixes, centralized here to
# avoid re-drift, but not registered as document types.
WORKFLOW_QUEUE_KEY_PREFIX = "workflow_queue:"
WORKFLOW_INVOKE_REQUEST_KEY_PREFIX = "workflow_invoke_request:"
WORKFLOW_APPROVAL_REQUEST_KEY_PREFIX = "workflow_approval_request:"
