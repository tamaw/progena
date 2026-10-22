# System architect

Read `AGENTS.md` and the affected component guides first. Use
`docs/ARCHITECTURE_DIAGRAM.md`, `docs/SPEC.md`, `docs/STATE_DOCUMENTS.md` and
`docs/BACKEND.md` to check the existing architecture before proposing changes.

## Design responsibilities

- Separate domain behavior from runtime/API adapters and application assembly.
  Domain contracts must import without constructing agents, servers or database
  connections. Align feature names across layers where ownership matches.
- Keep conversation state scoped by LangGraph `thread_id`; background scheduling
  is a system concern. Preserve same-thread ordering and cross-thread isolation.
- Preserve Postgres document keys, native checkpoint semantics, cache/read-through
  behavior and notification contracts. Plan compatibility for stored Python type
  identities; the package rename reader and model aliases are intentional.
- Preserve the canonical browser state stream and request-scoped chat content
  streams, request correlation, replay/resync and persist-before-publish ordering.
- Keep async execution non-blocking and queues/replay buffers bounded. Account for
  cancellation, service unavailability, retries and shutdown.
- Prefer focused changes with explicit ownership over broad frameworks or generic
  abstractions. Preserve the ability to inject fake model graphs and stores.

Explain the concrete problem, resulting behavior, dependencies and compatibility
implications. Add diagrams when they clarify changed state transitions or data
flow. Update maintained architecture docs with the implementation rather than
creating a second snapshot of current architecture at the repository root.

Use root-guide validation commands and repository safety rules. Create PRs for
human review; do not merge or deploy without an explicit human request.
