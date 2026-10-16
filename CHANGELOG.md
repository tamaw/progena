# Changelog

All notable changes to the Progenitor project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Document events for workflows**: every create/update/delete of a state document is appended to a durable `document_events` log (before/after payloads, writer origin) by a Postgres trigger; the backend consumes it from a persisted cursor, so events written while it is down are dispatched on restart
  - Document types are registered as `document_type:<id>` rows in Postgres (built-ins seeded at startup) and map key prefixes/keys to schemas under `data/schemas/`
  - Workflow event nodes subscribe by `document_type` with optional `key`, `operations`, `changed_fields`, and `match` (dotted paths such as `after.column_id`) filters, and expose `before`/`after` as whole-document ports typed with the document type's label
  - New `unpack` workflow node exposes an object port's properties as output ports (derived on save, chainable); the editor creates one by dragging an object port onto empty canvas or clicking its ▸, lists connected fields with a toggle to show every field, and confirms before re-wiring drops edges
  - New `document_read` and `document_query` workflow nodes read documents directly, without an agent call
  - Workflow runs tag their writes with their run id, so a workflow is never re-triggered by its own run documents
  - Replaces the `kanban.card.*` and `document.changed` event types, the in-memory kanban diff cache, and `/api/ui/document-kinds` (now `/api/ui/document-types`); event widgets use `<document_type>.<op>` names
  - Routine run lifecycle UI events (`agent.routine.run.*`) are published from the document event log; `StateReader.add_raw_watcher` is removed
  - The runtime's store listener now only refreshes chat-facing registries (MCP servers/tools, plugins, `agent:` documents); `repos`, `repo_scan`, and `background_tasks` are read through from Postgres by `StateManager` instead of cached and invalidated on NOTIFY, and the unused `touch_object_agent` / `agent-touch:` marker path is removed
- **Prompt template workflow nodes**: `extract` and `agent` nodes replace the old `agent` (message-based) and `mcp` nodes. Both render a prompt template (inline `template`, or a `prompt` document path) with values from their input ports (one per `{placeholder}` root) and send it to Progenitor as a normal chat turn. `extract` has Progenitor answer through its `structured_output` tool and reply with that JSON, which is validated against the result schema (node `schema` > template front matter `output_schema` > the typed input `result` is wired into). `agent` returns Progenitor's reply as `message` and can name a `subagent` to delegate to. All prompt steps in a run share one thread (a wired `thread_id` overrides), listed in Chat as `Workflow: <title>`.
- **Prompt templates**: prompt documents take optional YAML front matter (`title`, `description`, `inputs`, `output_schema`, `system`) and `{name}` / `{name.path}` placeholders; the agent's `/prompts/` mount is writable (front matter validated on write), and shipped prompts missing from Postgres are added on startup without overwriting edited ones
- **Workflow cron triggers**: a `cron` trigger node (`expression`, `timezone`, `overlap`) starts a workflow on a 5-field cron schedule and outputs `scheduled_for`, `fired_at`, `timezone`. The backend clock (`app/backend/workflow_cron.py`) sleeps until the next due node and replans when workflows change; a fire missed while the backend was down runs once on restart, paused workflows don't replay missed fires, and `overlap: skip` (default) drops a fire while a run is active (`queue` runs it afterwards). ▶ and Progenitor's `run_workflow` on a cron-triggered workflow fire it now. The editor previews the next runs via `GET /api/ui/workflows/cron-preview`. Progenitor's own schedules are unchanged except that schedule entries no longer accept `workflow_id`; cron and timezone evaluation is shared via `app/progenitor/cron.py`
- **Session documents** (`progenitor.state` / `session:<thread_id>`, `data/schemas/session.schema.v1.json`, `app/progenitor/sessions/`): every chat conversation is one jsonb document, created by Postgres, not Python. A trigger on `checkpoint_writes` (installed with the routine checkpoint trigger) decodes the first user message of a thread tagged `configurable.progenitor_source` = `ui`/`telegram`/`api` and inserts the session once (`ON CONFLICT DO NOTHING`, so a retried first turn can't create two); workflow and routine threads get none. Its `created` event is the "user started a chat" trigger; `first_message` is the opening text (`* sends an image` when it had none). After each turn the runtime writes the IRC-style transcript into `content` with an atomic jsonb merge; content/last-activity-only updates are not logged as document events. The sidebar lists session documents and follows their events live, so any writer's rename shows up at once. `backend.chat.sessions`, its `__index__`, draft rows, tombstones, `shared`, and checkpoint-only sidebar rows are removed; "+ New" is a local draft until the first send, and deleting a chat deletes only its session document.
- **Session migration** (`scripts/migrate_sessions.py`): one-off script that creates session documents for chats from before the cutover (main-agent threads keyed `<uuid>`/`thread_sess_*`, `tele_*`, `conv_*`), taking `title`/`pinned`/`archived` from an old `backend.chat.sessions` row when one exists, skipping chats marked deleted, rendering `content`, removing the `created` events (so no workflow fires), and deleting the old chat rows and transcript cache. Writes a `progenitor.migrations`/`sessions` marker and refuses to run twice. `--dry-run` lists what it would create.
- **`document_write` workflow node**: atomically merges the connected fields into an existing document of a type that declares `writable` fields (`session`: `title`, `pinned`, `archived`), tagged with the run's origin so it can't retrigger its own workflow. Outputs `found` and the written fields.
- **Workflow canvas**: the cursor shows grab while the right button is held to pan
- **Model profiles**: Added a separate searchable list/detail page at `/profiles`, backed by `/config/profiles.json`. Users can edit deployment and reasoning overrides in the same bordered configuration surface as agents, delete a profile from the detail header, restore environment inheritance, and receive live Postgres-driven updates in both the page and routine profile selectors. Progenitor can add, edit, or remove complete profile maps through its agent-writable virtual file. Drafts are preserved and saves blocked when the selected profile changes remotely.
- **Chat history mount for the agent** (`/sessions/`, `app/progenitor/sessions/`, `app/progenitor/agent/session_backend.py`): every non-archived chat is `<date>_<title-slug>_<id8>.md`: YAML front matter from its session document (stored `title`, computed `display_title`, `source`, flags, dates) followed by the IRC-style transcript — `<nick>` lines for what was said, `* nick` actions for reasoning titles, tool calls and images (failures read `can't …`), `-nick-` notices, join/part plus `-> *agent*` for subagent, async, A2A, and direct `@agent` turns. The user's nick and timezone come from account preferences; titles use the chat sidebar's rule (`sessions/titles.py`). Progenitor can edit the front matter's `title`, `pinned`, and `archived`; any other change is rejected. The `reading-sessions` skill documents the format.
- **Runtime Responses API contract** (`app/progenitor/runtime/http_chat.py`, `runtime/responses_api.py`): the runtime now serves an OpenAI Responses/Conversations-compatible API backed by native LangGraph — `POST /v1/responses` (streaming and non-streaming), `POST /v1/responses/{id}/cancel`, `GET /v1/conversations/{id}/items`, `DELETE /v1/conversations/{id}`. See `docs/SPEC.md` for vendor extensions.
- **Per-tool MCP enable/disable for the agent** (`/config/mcp_tools.json`, `app/progenitor/agent/state_backend.py`): the agent can now turn a single MCP tool on or off by flipping its `enabled` flag, the same flag the Tools page toggles, instead of disabling the whole server or editing its URL. Only `enabled` is editable; the tool list stays discovery-owned and any other edit is rejected with an explanation. Entries are validated against the new `data/schemas/mcp_tool_entry.schema.v1.json`, and the `managing-mcp` skill documents the flow (find the entry, schema + `structured_output`, edit, verify with `mcp`).
- **DeepAgent Integration** (`app/progenitor/agent/`): Selective adoption of LangChain's `deepagents` library
  - `deep_agent.py`: Factory creating configured DeepAgent with LiteLLM routing, tools, middleware, skills, memory, and subagents
  - `langchain_adapter.py`: ChatOpenAI → LiteLLM proxy bridge with Azure structured output bypass
  - `tools_adapter.py`: Auto-discovers and wraps 23+ BaseSystemTool instances as DeepAgent-compatible StructuredTools
  - `backends.py`: CompositeBackend with persisted `/memories/` on FilesystemBackend, read-only scoped filesystem mounts, and StateBackend workspace
  - `middleware.py`: Custom middleware — SecretRedactionMiddleware, AuditLoggingMiddleware
  - `subagents.py`: Build loop as CompiledSubAgent, code reviewer, and researcher subagents
- **Skills System** (`prompts/skills/`): 5 SKILL.md files with YAML frontmatter for progressive disclosure
  - `build-agent`: A2A agent and MCP tool building patterns
  - `scheduling`: Task scheduling with natural language time parsing
  - `git-workflow`: Branching, worktrees, PRs, merge conflict resolution
  - `self-improvement`: Iteration analysis, pattern extraction, anti-criteria learning
  - `agent-architecture`: Dual-loop architecture, state persistence, A2A protocol reference
- **Runtime Adapter** (`app/progenitor/runtime/deep_agent_adapter.py`): Drop-in DeepAgent integration for ProgenitorLoop
- **Memory**: Auto-discovers AGENTS.md for system prompt injection, with persisted `/memories/` files stored on disk under `data/memories`
- **Mock LLM Client** (EN-004): Demo mode with `PROGENITOR_MOCK_LLM=true` environment variable
- **Tiktoken Integration**: Accurate token counting for OpenAI models with graceful fallback
- **Tokenizer Module**: New `app/progenitor/llm/tokenizer.py` with `count_tokens()`, `count_message_tokens()`, `truncate_to_tokens()` functions
- **Pre-commit Configuration**: Automated linting and formatting with ruff, black, and mypy

### Changed
- **Dashboard**: Replaced the dated page with a responsive, theme-aware workspace overview covering recent workflow runs, pending reviews, upcoming enabled schedules, runtime resource search/status filters, inline logs, and collapsible runtime diagnostics. Dashboard collections hydrate through the canonical notification stream and recover after reconnect.
- **Chat history reads go through the runtime** (`app/backend/runtime_conversations.py`): the backend no longer rebuilds messages from checkpoint blobs; it pages the runtime's conversation items (cached per checkpoint, pooled connections, off the event loop). Fixes chats rendering as an empty "New chat" when their latest checkpoint held a `DeltaChannel` snapshot. While the runtime is unreachable, chats keep their last known history, or list as "history unavailable" with a 503 on open, instead of looking empty.
- **Backend ↔ runtime chat transport**: `SSEProgenitorClient` uses `/v1/responses`; `/v1/chat` and `/v1/chat/{id}/cancel` are removed. A request without a `conversation` now runs in a fresh `conv_` conversation instead of the runtime's main thread; Telegram turns use a per-chat `tele_` conversation.
- **Tool results in conversation items**: `function_call_output.output` keeps multi-part tool results as Responses input parts (text, images) instead of a Python repr.
- **Stopping a reply keeps it**: when a turn is cancelled, the runtime saves the text streamed so far to the conversation (native `aupdate_state`, marked cancelled). After a reload the chat shows `… [Cancelled]` instead of losing the reply, and the next turn sees it, so "continue" picks up where it stopped.
- **`langgraph-checkpoint-postgres` pinned to the langgraph#8556 fix commit** (`pyproject.toml`): reading a checkpoint more than 1,024 checkpoints back in a thread (DeltaChannel messages) silently returned no messages on 3.1.2 (langgraph#8448). No PyPI release has the fix yet; revert to a version pin once one does. Existing virtualenvs need `pip install --force-reinstall --no-deps` of the pinned URL, since the commit still reports version 3.1.2.
- **Schedule run results** read run threads with the agent graph's `aget_state` instead of the backend document store.
- **E2E routine fixtures are self-contained**: `scripts/seed_e2e_fixtures.py` is removed. The connector, editing and sequences-redirect specs insert their own uniquely-suffixed copies of the graphs in `app/frontend/e2e/fixtures/routine-graphs.json` (`seedGraphFixtures` in `e2e/helpers/routineFixture.mjs`) and delete them afterwards, so the suite needs no seed step and leaves nothing behind. `tests/backend/test_e2e_routine_fixtures.py` keeps the graphs valid.
- **Conversation Compressor**: Now uses tiktoken for accurate token estimation
- **Exception Handling**: Added DEBUG logging to previously silent exception handlers (CQ-009)
- **Document event log reads in commit order**: `document_events` and `document_event_cursors` carry the writing transaction id (`txid xid8`, added in place on existing tables, all pre-existing rows sharing one txid). The dispatcher reads `(txid, seq)` positions and only transactions older than every open one (`pg_snapshot_xmin`), so an event whose transaction commits late is never skipped; the 5-second sequence-gap timer is gone.
- **Document event dispatch fails fast**: a handler exception stops the backend dispatcher (`DocumentEventDispatcher.stopped`) with the failing event unacknowledged, instead of logging it and moving past it; it resumes from that event on restart. Workflow failures are still recorded on the run and don't stop dispatch.
- **`human_input` workflow node removed**: workflows keep `approval` as their only human step; human-in-the-loop work belongs in routines (`human` node).
- **Prompt references are paths relative to `/prompts/`** (`workflows/card.md`), validated by one rule (`prompt_relative_path`) for the agent's `/prompts/` mount, the prompt API and workflow `prompt` fields: relative, no `..`, `.md` only.
- **Model profile names must be unique after trimming whitespace**; a catalog with `" general "` and `"general"` is rejected.

### Fixed
- Document event dispatch no longer stalls when an event is held back by an older open transaction that then ends without writing an event; the dispatcher re-checks every second while events are deferred.
- Renaming a chat while the runtime is down succeeds (was a 503 although the rename applied) and updates the sidebar instead of stopping document event dispatch.
- The agent's `/sessions/` folder supports `glob`, rejects session field values the schema forbids (e.g. a numeric title) and ambiguous `edit_file` matches; transcript renders of one chat no longer race.
- Session `first_message` and derived titles no longer include the machine-only attachment note or unredacted secrets.
- Cron workflows fire each due time once across backends sharing a database, don't replay a pause+resume that happened between ticks, and keep separate state for ids containing `:`.
- `mutate_document` locks the existing row, so plain writes can't be lost under it; the in-memory store publishes events atomically and filters session noise like Postgres.
- Workflows: a `document_write` with nothing to write no longer fails; `{thread_id}` renders the run thread and `thread_id` outputs the thread actually used; mid-run data edits reach later nodes; query filters tell `true` from `1`; Unpack handles `$ref`'d and nullable objects; integer outputs connect to number inputs; prompt front matter needs an exact `---`; inline input schemas referencing their own properties stay valid when embedded.
- A routine subagent validates its input against the same graph it runs.
- Chat: + New while a reply streams leaves the new chat usable; a new chat owns its reconciliation once saved; opening another unsaved thread clears the previous transcript; reopening quick chat mid-reply no longer duplicates text.
- Workflow editor: editing an Extract prompt keeps its typed result and Unpack edges; the prompt document picker lists prompts without first visiting the Prompts page, and choosing one creates its input ports; deleting a node prunes dependent Unpack edges; palette node resizes persist; prompt nodes keep the optional `thread_id` input; the node menu no longer clips on short screens.
- Profile notifications now bypass unrelated state-snapshot diffs; profile queries and edits use `202` acknowledgements plus correlated `profiles.snapshot` events instead of successful HTTP response-body data.
- Snapshot-confirmed saves no longer have their success status overwritten by the acknowledgement-waiting effect; request failures preserve the draft and show the error.
- Schedule and inbox document notifications now reach their collection subscribers even when a sibling document from the same transaction has already refreshed the shared state snapshot.
- `/config/mcp_tools.json` rejected nothing outside `tools`: an agent edit that added its own top-level key (e.g. a `tool_overrides` map) was silently dropped and reported as success while no tool changed. Any change outside a tool's `enabled` flag is now rejected with a message pointing at the flag.
- `grep` and `glob` on the agent's virtual folders (`/config`, `/tasks`, `/inbox`, `/workflows`, `/routines`, `/policies`, content documents) always returned nothing, so the agent was told text it had just read didn't exist (this is how it ended up inventing a `tool_overrides` key). They now search the documents as `read_file` renders them, with matching line numbers.
- Progenitor-invoked event workflows now run their steps: the request input fills the trigger node's declared ports (`key`, `after`, …) and is validated against them, instead of completing with no node executed.
- Resuming a paused cron workflow no longer fires a due time from the paused period; restarting the backend under a new event loop no longer kills the cron clock.
- Pausing a workflow while one of its prompt steps runs now holds; the step's checkpoint no longer overwrites the pause.
- Concurrent model profile edits/deletes no longer lose each other or bring a deleted profile back (`mutate_document` under a per-key transaction lock).
- Workflow validation: `$defs` survive extract schema inference and typed template inputs/outputs (`inputs: {flow: workflow}`); a `string` output connects to a `["string", "null"]` input (editor and backend); `true` → `1` counts as a changed field and no longer matches an event filter on `true`; malformed `filter_fields` and non-string front matter keys are validation errors instead of 500s; `workflow_run` documents accept `source: "cron"`.
- Progenitor can save workflows whose steps reference prompt documents.
- Workflow editor: `false`/`0`/`""` data values, saved node sizes, schema edits (now enabling Save), Configure/Unpack after applying JSON, Cut on an Unpack input, and Unpack fields of palette-added nodes all behave like other edits; the node options dialog scrolls instead of clipping.
- Quick chat closed mid-reply and reopened after it finished shows the reply instead of "Thinking" forever; a failed agent-roster fetch is retried on reconnect.
- Prompt/profile saves time out 15s after acknowledgement even while other updates arrive, and a save cancelled by a disconnect can't report its error on the next selection.
- Telemetry exception handlers now log at DEBUG level instead of silently passing

## [0.6.0] - 2026-01-29

### Added
- **Sprint 5: Chat UI** - Complete web interface for chat interactions
  - Real-time message streaming with token counter
  - Markdown rendering with syntax highlighting
  - Conversation management (export, clear, keyboard shortcuts)
  - Visual thinking indicators and sub-agent monitoring
  - Dark/light theme support
  - Mobile-responsive design
  - Accessibility features (ARIA labels, keyboard navigation)
  - Metrics dashboard for response times
  - 14 new JavaScript modules

### Changed
- Backend now serves static UI files from `/ui/` endpoint
- API responses include thinking indicators via metadata field

## [0.5.0] - 2026-01-28

### Added
- **Sprint 4: IPC Protocol** - Length-prefixed JSON protocol between backend and progenitor
  - Bidirectional async streaming
  - Heartbeat and health monitoring
  - Automatic reconnection with exponential backoff
  - Request ID correlation across components

### Fixed
- CR-001: Subprocess IPC fragility - Added heartbeat monitoring and auto-restart
- CR-002: Missing input validation on WebSocket/streaming endpoints
- CR-003: Git worktree cleanup race condition

## [0.4.0] - 2026-01-27

### Added
- **Sprint 3: Observability** - Langfuse integration for LLM tracing
  - Telemetry decorators for automatic instrumentation
  - Metrics collection (counters, histograms, gauges)
  - JSONL export for log aggregation
  - Request ID correlation headers

### Changed
- All nodes now automatically instrumented with telemetry
- LLM calls tracked with token usage metrics

## [0.3.0] - 2026-01-26

### Added
- **Sprint 2: Reliability** - Production-ready infrastructure
  - Bounded input queues to prevent memory exhaustion
  - Rate limiting with per-client tracking
  - Graceful shutdown with configurable timeout
  - Docker health checks and resource limits
  - Secret redaction in logs

### Security
- SEC-001: Command injection protection with allowlist
- SEC-002: Secret redaction pattern for API keys

## [0.2.0] - 2026-01-25

### Added
- **Sprint 1: Architecture** - Core dual-loop system
  - LangGraph-based workflow engine
  - Composable state types with TypedDict
  - Dependency injection for nodes
  - Ideal state refinement loop
  - Tool registry with semantic search

### Changed
- Migrated from simple chain to dual-loop architecture
- State management now uses immutable updates

## [0.1.0] - 2026-01-24

### Added
- Initial project setup
- Basic chat completion with OpenAI/Azure OpenAI
- FastAPI backend with OpenAI-compatible API
- Pydantic configuration management
- Basic test suite

---

## Categories

- **Added**: New features
- **Changed**: Changes to existing functionality
- **Deprecated**: Features to be removed in future versions
- **Removed**: Features removed in this release
- **Fixed**: Bug fixes
- **Security**: Security-related changes

[Unreleased]: https://github.com/your-org/progenitor/compare/v0.6.0...HEAD
[0.6.0]: https://github.com/your-org/progenitor/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/your-org/progenitor/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/your-org/progenitor/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/your-org/progenitor/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/your-org/progenitor/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/your-org/progenitor/releases/tag/v0.1.0
