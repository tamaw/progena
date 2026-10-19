# Changelog

All notable changes to the Progenitor project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Workspace-session editor drafts**: switching pages or selecting another item retains unsaved graph, raw JSON, Markdown, configuration, title and creation-form edits, along with the last detail selection and editor context. Blur no longer discards title drafts. Saves settle only their originating entity/revision after canonical confirmation; failures and reconnect snapshots preserve unfinished work, and a save interrupted by a disconnect or forced reconnect before its acknowledgement unlocks its editor with the draft intact. Deleting a profile, knowledge base or routine node discards its retained drafts. Explicit Cancel/Reset discards the corresponding edit; reload, tab closure and workspace changes clear in-memory drafts.
- **Hidden frontend event debugger**: Ctrl+Shift+E (Cmd+Shift+E on macOS) opens a right-side event stream without adding a header action. It reads Redux's existing 500-event canonical buffer, including heartbeats, with newest-first rows, expandable full JSON, type/event/request-ID filtering and a local-only clear view. A default-on "Hide status updates" switch removes periodic `status.updated` rows from view without changing Redux. Offscreen rows skip layout work while the complete buffer remains available. The panel supports docking while interacting with the app and never restores itself on reload.
- **Frontend connection recovery**: after 20 seconds without a status update, the shared modal shows "Waiting to reconnect", a 10-second reconnect-attempt countdown, and manual reconnect/refresh actions. Short interruptions remain silent; only a new status update dismisses the modal. Reconnect preserves the page, settles old pending requests, and ignores late frames from replaced streams.
- **Local frontend design library** (`/design`): development-only catalogue of current theme tokens, typography, branding, shared controls and navigation/layout/feedback/data/chat patterns, with interactive local fixtures and source references. Available by direct URL only on loopback browser hostnames; the page and styles are excluded from production builds.
- **Frontend primitive consistency**: reuse shared dropdowns in profile/repository forms and routine node/policy controls, shared model/schedule detail cards and title-action icons, and the standard checkbox switch for tool and run-parameter toggles. Switches now show keyboard focus and disabled state; graph dropdowns remain portal-backed, with viewport-aware menu placement. Corrected undefined font and theme-token references without restructuring page layouts or editors.
- **Shared frontend presentation components**: workflow/routine JSON editors now share one highlighted input and theme-aware styles; instruction, knowledge, plugin and quick-chat previews share a read-only Markdown body while retaining their existing sanitization, links and diagram behavior. Confirmation, new-agent, workflow-options and container dialogs share focus containment/restoration and guarded Escape/backdrop dismissal. Tools metric cards and schedule header statuses now use the existing shared components; `/design` includes JSON and Markdown examples.
- **Shared frontend interaction lifecycles**: pages reuse scoped mutation/confirmation and inline-title helpers, Agents/Routines share catalogue behavior, and conventional menus, hydrated route guards, local selection and matching date labels share focused utilities. Errors use one operation-aware presentation contract, cancellation stays quiet, failed destructive actions keep their warning and retry, and detail caches track per-key loading/failures until a correlated stream event arrives without hiding cached content. Frontend unit JSX now uses the same automatic runtime as the application.
- **Consistent detail section headings**: Models, Channels, Schedules and Tasks now place section titles/actions above their content instead of inside a second outer card, matching Agents and Profiles. Task metadata retains a separate bordered content surface; redundant page-specific section overrides are removed.
- **Workflow save errors name the fix** (`app/progenitor/workflows.py`, `app/progenitor/structured.py`): a rejected edge lists the node's real input or output ports with their types, an unwired node lists the inputs it can take, a type mismatch shows both types, and JSON-schema rejections include the field path (`at $.version: 3 was expected`). The managing-workflows and maintaining-instructions skills now spell out instruction input types (a bare `string` is rejected; use `{type: string}` or a document type id) and tell the writer to fix and re-save on rejection instead of stopping.
- **Interactive Claude ACP sessions**: `acp_start`, `acp_send`, `acp_status`, `acp_answer`, `acp_cancel`, and `acp_list` let Progenitor and delegated agents steer a live turn, wait without fast polling, answer Claude questions and plan submissions, select ACP modes, and cancel only when necessary. ACP task documents now expose requester-owned `messages`, `mode`, and `answers`, and supervisor-owned `pending_input`, so workflows can react to status, stalled, and pending-input events.
- **Routine agent instructions**: routine `agent` nodes can use `instruction` paths relative to `/instructions/` as an alternative to inline `prompt_template`; templates render with routine state. The new `driving-acp-agents` skill documents safe Claude/session control and worktree handoff.
- **ACP SDK upgrade**: `agent-client-protocol` 0.7.1 → 0.12.1 everywhere (runtime, backend, builder and greetings agents), for elicitation, session modes, configuration options, and session close.
- **Claude commits under its own identity** (`docker/claude/entrypoint.sh`): the Claude container sets a global git `user.name`/`user.email` (`CLAUDE_GIT_NAME`/`CLAUDE_GIT_EMAIL`, default `Claude (Progenitor)`), so it can commit in worktrees; publishing stays with `repo-manager`.
- **A2A task-session tools replace the legacy single dispatcher** (`a2a_send`, `a2a_send_async`, `a2a_status`, `a2a_cancel`, and `a2a_list`): sends now require an explicit agent, support task/context follow-ups and `input-required` replies, preserve live progress logs, and cleanly reject ACP transports in favor of `acp_start`.
- **ACP tasks are `acp_task:<id>` documents driven by the backend** (`app/backend/acp_supervisor.py`, `acp_task.schema.v1.json`): one backend supervisor runs every ACP turn, for `call_agent` and workflows alike, so workflows can drive Claude in a worktree and chain on its progress
  - Requests are document writes: create with `status: "queued"` to start a session; write `{prompt, status: "queued"}` to run the next turn on the same session (ACP `session/resume`; a running turn is cancelled first, which is how a stalled agent is nudged); write `{cancel_requested: true}` to cancel. Supervisor writes are atomic and touch only its own fields, so a request written mid-turn is never lost
  - The supervisor keeps `status` (`queued`/`working`/`completed`/`failed`/`canceled`/`interrupted`), `session_id`, `turn`, `output`, `history`, `tool_calls`, `progress` (last message, plan, tool-call count, last tool, last activity), and `stalled`. Limits and cadence: `PROGENITOR_ACP_MAX_SESSIONS` (2), `PROGENITOR_ACP_MAX_TURNS` (20), `PROGENITOR_ACP_PROGRESS_INTERVAL_SECONDS` (15), `PROGENITOR_ACP_STALL_SECONDS` (600)
  - Restarts: turns cut off by a backend restart become `interrupted` instead of staying `working` forever; queued tasks start on boot. The single `acp_tasks` list document is migrated into per-task documents on first start
  - A configured `model` is selected with `session/set_config_option` for agents that expose models as config options (Claude ACP 0.93), else `session/set_session_model`; permission requests pick an allow option
- **The `repo-manager` agent can publish** (`data/agents/objects/repo-manager/SYSTEM.md`, `data/shims/bin/{git,gh}`): a new `git_publish: true` manifest flag (bundled agents with `git_write` only) lets the wrapped `git` push named branches other than main/master — never forced, no refspecs, no implicit push — and `gh pr create`. `git_write` agents may now `git worktree remove` (unforced, so uncommitted work blocks it). Network git commands authenticate to GitHub with the passed-through `GH_TOKEN` via gh's credential helper, keeping it out of argv and remote URLs. repo-manager opts in and now documents cloning, per-branch worktrees under `$PROGENITOR_REPOS_DIR/<repo-id>.worktrees/`, and publishing, so a routine `agent` step can do all repository work
- **Routine and workflow skills point repository work at `repo-manager`**, and the routine skill documents the 120-second default node `timeout` that shell-heavy agent steps need to raise
- **New routines are runnable from chat without a restart** (`RoutineSubagentRegistry`, `app/progenitor/agent/object_agent_registry.py`): routine subagents were built once at startup, so a routine Progenitor had just written was rejected as an unknown `task` subagent. They are now rebuilt when a `routine:` document changes, and a `task` call for an unknown name refreshes both the object-agent and routine registries before failing, which covers a routine written earlier in the same turn
- **Kanban cards link their work**: optional `repo_id` and `acp_task_id` fields (schema, card API, task files, workflow writes)
- **Workflow runs recover after a backend restart**: a run left `running` (not waiting on a routine) is failed as interrupted and the workflow's queue moves on; queued runs start
- **Alerts are `alert:<id>` documents** (`app/backend/alerts.py`, `alert.schema.v1.json`): alerts survive restarts (newest 100 kept) and have one delivery path — producers create an alert document, and the backend's document-event handler shows it in the alert center and asks Progenitor to relay it (chat, or Telegram when away). Progenitor raises alerts by writing `/alerts/<id>.json`.
- **Instructions** (renamed from Prompts; `data/instructions/`, `/instructions/` mount, `instruction:` documents and document type, `/api/ui/instructions`, `app/progenitor/instructions/`, `app/backend/instruction_service.py`, `InstructionsPage.jsx`): one kind of markdown template for everything sent to a model. Front matter is the template format (`title`, `description`, `inputs`, `output_schema`, `system`) and `{placeholders}` are read from the body; loaders strip the front matter. Workflow `extract`/`agent` nodes reference an instruction with `instruction:` (was `prompt:`). Who may edit depends on the group: `progenitor/` is read by the runtime, editable but its placeholders are fixed (edits to `genesis.md` apply on the next model call); `builder_agent/`, `builder_progenitor/` and `observe_agent/` are bundled with their A2A agent, which reads its own files, and are read-only; any other group (e.g. `workflows/`) is a freely editable workflow template. Every version is kept in the immutable `instruction_revisions` table and can be viewed and restored. The page sits in the sidebar under Delegate, matches the other list/detail pages, and links instructions at `/instructions/<group>/<file>.md`. The runtime syncs `data/instructions/` on every boot. Agent discovery's missing user instruction is restored (`progenitor/agent_discovery_analysis.md`), `decide.md` moved to `builder_agent/`, the unused `progenitor/system.md` and `scheduled_prefix.md` were removed, and `PROGENITOR_PROMPTS_DIR` is now `PROGENITOR_INSTRUCTIONS_DIR`.
- **Inbox message revisions**: backend startup now installs a Postgres `store` trigger and backfills immutable, content-hashed snapshots of full inbox message payloads (body and frontmatter) into `inbox_message_revisions`. Agent writes, user metadata changes, and the final version before deletion are captured; identical payloads share a revision. No history UI or restore action yet.
- **Structured inbox content**: Markdown headings `##`–`######` create selectable sections bounded by the next same-or-higher heading, a `<questions>` block, or end of document. Hovering reveals AI Edit, Explain, and Remove actions; these send instructions containing the current section and inbox file path to Progenitor through quick chat rather than writing message bodies in the UI. AI Edit supports an inline instruction popover and an unsent quick-chat pop-out. Inbox document events display the latest agent-owned body without merging writes or attributing an update to a particular prompt. `<questions>` choices and free-text answers instruct the agent to rewrite the question while retaining its options. Markdown tables share chat styling, and code examples do not create selectable sections.
- **Inbox heading presentation**: selectable sections display semantic Markdown heading levels without literal `##` markers or the former small tag-label typography. Section brackets begin below headings while heading clicks still select the whole section; question brackets now group the question and its answer controls. The managing-inbox skill includes a nested heading example.
- **Workflow format nodes and create defaults**: `format` deterministically renders a plain-text template without an LLM, with required input ports derived from placeholders and a string `text` output. `document_create` now treats writable schema properties with `default` values as optional ports and deep-copies their defaults when unwired or `null`.
- **Pause notifications** (`notify_on_pause` workflow setting, "Notify when paused" in the editor): a run pausing at an approval node or a routine's `human` step raises an alert with the question, the allowed answers, what is waiting (workflow run or routine thread) and who may answer
- **Progenitor can answer pauses, unless only a person may**: new `resolve_workflow_approval` tool (applied by the backend from a `workflow_approval_request:` document); workflow approval nodes and routine `human` nodes take `approver: "any"` (default) or `"human"`. Human-only steps are refused on Progenitor's paths — the approval request is rejected by the backend, `resume_routine` by the runtime — and stay answerable in the UI. Routine `paused` lifecycle events now carry the question, allowed answers and approver
- **Workflow `routine` node** (`app/progenitor/workflows.py`, `app/backend/workflow_service.py`, `app/backend/routine_runs.py`): a workflow step runs one routine deterministically, on the same `routine_run` queue as the Agents page's Run button, instead of asking Progenitor to delegate to it
  - Ports are derived from the routine graph on save: its `input_schema` fields, or one `message` string when it declares none; its `output_schema` fields, or `message` (the final reply), plus `thread_id`. An unknown routine id fails the save; the editor's Routine node lists routines from the agent roster and drops connections to ports the newly picked routine doesn't have
  - The run waits on the routine's lifecycle events (`run.pending_routine`): `completed` stores the routine output (now carried on completed `routine_run_event:` documents) and continues, `failed` fails the run, and a routine paused at a `human` node pauses the workflow run until it is answered; resuming the workflow alone doesn't skip the human step
- **Repositories are one `repo:<id>` document each** (`app/backend/repo_service.py`, `app/progenitor/agent/state_backend.py`, `scripts/migrate_repos.py`): the single `repos` document (`{"repos": {"<id>": ...}}`) is split into per-repo documents typed by a new `repo` document type (`repo_entry.schema.v1.json`)
  - Workflows can trigger on a repo being created/updated/deleted with typed `after.name`/`after.local_path`/... ports; each write touches only its own repo under that key's lock (create claims a free `<slug>`, `<slug>-2`... id), so concurrent edits of different repos no longer overwrite each other
  - The agent edits declared repos as `/config/repos/<id>.json` (replacing `/config/repos.json`); `/repos/` stays the read-only checked-out source. Direct document writes (e.g. the agent's) now update the Repositories page live
  - `python scripts/migrate_repos.py [--dry-run]` splits an existing `repos` document once (keeps each `created_at`, suppresses the `created` events, removes the old document)
- **repo-manager can set repositories up** (`data/agents/objects/repo-manager/SYSTEM.md`, `data/shims/bin/{git,gh}`): bundled shell agents can opt into `git_write: true`, which lets the wrapped `git`/`gh` run repository setup — `clone`, `fetch`, `pull --ff-only`, `switch`, `worktree add`, `submodule`, `branch` create/upstream, `remote add`/`set-url`, `gh repo clone`. `commit`, `reset`, `rebase`, `merge`, `clean`, `rm`, `checkout` (it can discard work; use `switch`), forced switches and branch deletion stay refused for every agent; `push` only with `git_publish` (above)
  - repo-manager opts in, reads declared repos from `/config/repos/`, and clones into its `local_path` (relative paths under the new `$PROGENITOR_REPOS_DIR` shell variable); it never clones over an existing directory
- **Claude ACP container is part of the deployed stack** (`docker/claude/`, `docker-compose.progenitor.yml` service `claude`, `ci.yml`): built and redeployed by CI like the other images when `docker/claude/` changes, with a healthcheck and no host port (reachable as `tcp://progenitor-agent-claude:3001` on the stack network only). Authenticates with `CLAUDE_CODE_OAUTH_TOKEN` (a `claude setup-token` token, `cronix` environment secret). `claude-agent-acp` upgraded 0.44.0 → 0.93.0 (Claude Code 2.1.170 → 2.1.300); runs as UID 10001 to share `/app/repos` with the runtime; its stderr now goes to the container log instead of corrupting the ACP JSON-RPC stream

- **Document events for workflows**: every create/update/delete of a state document is appended to a durable `document_events` log (before/after payloads, writer origin) by a Postgres trigger; the backend consumes it from a persisted cursor, so events written while it is down are dispatched on restart
  - Document types are registered as `document_type:<id>` rows in Postgres (built-ins seeded at startup) and map key prefixes/keys to schemas under `data/schemas/`
  - Knowledge page (`knowledge:<base-id>/...`) and knowledge-index status (`knowledge_index`) changes are typed `knowledge_page` and `knowledge_index` events, so graph-native workflows can react through normal event nodes; index status is read-only.
  - Workflow event nodes subscribe by `document_type` with optional `key`, `operations`, `changed_fields`, and `match` (dotted paths such as `after.column_id`) filters, and expose `before`/`after` as whole-document ports typed with the document type's label
  - New `unpack` workflow node exposes an object port's properties as output ports (derived on save, chainable); the editor creates one by dragging an object port onto empty canvas or clicking its ▸, lists connected fields with a toggle to show every field, and confirms before re-wiring drops edges
  - New `document_read` and `document_query` workflow nodes read documents directly, without an agent call
  - Workflow runs tag their writes with their run id, so a workflow is never re-triggered by its own run documents
  - Replaces the `kanban.card.*` and `document.changed` event types, the in-memory kanban diff cache, and `/api/ui/document-kinds` (now `/api/ui/document-types`); event widgets use `<document_type>.<op>` names
  - Routine run lifecycle UI events (`agent.routine.run.*`) are published from the document event log; `StateReader.add_raw_watcher` is removed
  - The runtime's store listener now only refreshes chat-facing registries (MCP servers/tools, plugins, `agent:` documents); `repos`, `repo_scan`, and `background_tasks` are read through from Postgres by `StateManager` instead of cached and invalidated on NOTIFY, and the unused `touch_object_agent` / `agent-touch:` marker path is removed
- **Prompt template workflow nodes**: `extract` and `agent` nodes replace the old `agent` (message-based) and `mcp` nodes. Both render a prompt template (inline `template`, or an `instruction` path) with values from their input ports (one per `{placeholder}` root) and send it to Progenitor as a normal chat turn. `extract` has Progenitor answer through its `structured_output` tool and reply with that JSON, which is validated against the result schema (node `schema` > template front matter `output_schema` > the typed input `result` is wired into). `agent` returns Progenitor's reply as `message` and can name a `subagent` to delegate to. All prompt steps in a run share one thread (a wired `thread_id` overrides), listed in Chat as `Workflow: <title>`.
- **Workflow cron triggers**: a `cron` trigger node (`expression`, `timezone`, `overlap`) starts a workflow on a 5-field cron schedule and outputs `scheduled_for`, `fired_at`, `timezone`. The backend clock (`app/backend/workflow_cron.py`) sleeps until the next due node and replans when workflows change; a fire missed while the backend was down runs once on restart, paused workflows don't replay missed fires, and `overlap: skip` (default) drops a fire while a run is active (`queue` runs it afterwards). ▶ and Progenitor's `run_workflow` on a cron-triggered workflow fire it now. The editor previews the next runs via `GET /api/ui/workflows/cron-preview`. Progenitor's own schedules are unchanged except that schedule entries no longer accept `workflow_id`; cron and timezone evaluation is shared via `app/progenitor/cron.py`
- **Session documents** (`progenitor.state` / `session:<thread_id>`, `data/schemas/session.schema.v1.json`, `app/progenitor/sessions/`): every chat conversation is one jsonb document, created by Postgres, not Python. A trigger on `checkpoint_writes` (installed with the routine checkpoint trigger) decodes the first user message of a thread tagged `configurable.progenitor_source` = `ui`/`telegram`/`api` and inserts the session once (`ON CONFLICT DO NOTHING`, so a retried first turn can't create two); workflow and routine threads get none. Its `created` event is the "user started a chat" trigger; `first_message` is the opening text (`* sends an image` when it had none). After each turn the runtime writes the IRC-style transcript into `content` with an atomic jsonb merge; content/last-activity-only updates are not logged as document events. The sidebar lists session documents and follows their events live, so any writer's rename shows up at once. `backend.chat.sessions`, its `__index__`, draft rows, tombstones, `shared`, and checkpoint-only sidebar rows are removed; "+ New" is a local draft until the first send, and deleting a chat deletes only its session document.
- **Session migration** (`scripts/migrate_sessions.py`): one-off script that creates session documents for chats from before the cutover (main-agent threads keyed `<uuid>`/`thread_sess_*`, `tele_*`, `conv_*`), taking `title`/`pinned`/`archived` from an old `backend.chat.sessions` row when one exists, skipping chats marked deleted, rendering `content`, removing the `created` events (so no workflow fires), and deleting the old chat rows and transcript cache. Writes a `progenitor.migrations`/`sessions` marker and refuses to run twice. `--dry-run` lists what it would create.
- **`document_write` workflow node**: atomically merges connected writable schema fields into an existing document, tagged with the run's origin so it can't retrigger its own workflow. Outputs `found` and the written fields.
- **Schema-driven workflow document nodes**: document writability now comes from JSON Schema (`readOnly: true` is never writable) rather than a per-type allowlist. New atomic `document_create` and `document_delete` nodes operate on collection documents with run-origin tagging; `document_query` now supports typed operator filters, ordering, and pre-limit `total` counts.
- **Workflow canvas**: the cursor shows grab while the right button is held to pan
- **Model profiles**: Added a separate searchable list/detail page at `/profiles`, backed by `/config/profiles.json`. Users can edit deployment and reasoning overrides in the same bordered configuration surface as agents, delete a profile from the detail header, restore environment inheritance, and receive live Postgres-driven updates in both the page and routine profile selectors. Progenitor can add, edit, or remove complete profile maps through its agent-writable virtual file. Drafts are preserved and saves blocked when the selected profile changes remotely.
- **Chat history mount for the agent** (`/sessions/`, `app/progenitor/sessions/`, `app/progenitor/agent/session_backend.py`): every non-archived chat is `<date>_<title-slug>_<id8>.md`: YAML front matter from its session document (stored `title`, computed `display_title`, `source`, flags, dates) followed by the IRC-style transcript — `<nick>` lines for what was said, `* nick` actions for reasoning titles, tool calls and images (failures read `can't …`), `-nick-` notices, join/part plus `-> *agent*` for subagent, async, A2A, and direct `@agent` turns. The user's nick and timezone come from account preferences; titles use the chat sidebar's rule (`sessions/titles.py`). Progenitor can edit the front matter's `title`, `pinned`, and `archived`; any other change is rejected. The `reading-sessions` skill documents the format.
- **Semantic search over knowledge pages** (`app/backend/knowledge_indexer.py`, `app/progenitor/knowledge/index.py`, `app/progenitor/tools/search.py`): a backend indexer embeds concept pages (split by heading, ≤ 1,500 chars per chunk) into the new `knowledge_chunks` table whenever pages, the catalog, or the indexer settings change, re-embedding only pages whose content hash changed. One backend process indexes at a time (Postgres advisory lock); if it stops or loses its database connection, another process takes over within seconds, and every process forwards index status to its own browsers. Embeddings use the Azure OpenAI deployment named by `/config/indexer.json` (`{"profile": "<name in /config/profile.json>", "dimensions": 1536, "min_similarity": 0.25}`); changing the deployment or dimensions rebuilds the index. The new `search` tool ranks pages with pgvector cosine + Postgres full-text (reciprocal rank fusion) and returns only pages that match by keyword or reach `min_similarity` (cosine, 0–1), reporting each result's `similarity` and `keyword_match`; it never waits for indexing and lists pages that aren't indexed yet. Object agents with knowledge grants get `search` scoped to their bases. Indexing progress is a read-only `/knowledge/indexer.json`, `search_index` front matter on each base's root `index.md` (shown in the page header), UI events `knowledge.index.started|progress|completed|failed`, and workflow triggers `knowledge.index.completed|failed`. The Knowledge list shows Indexed / Indexing n/m / Failed.
- **Global search** (`app/backend/search_routes.py`, `GET /api/ui/search`, `SearchPanel`): the top-nav Search panel asks the backend once per query (202 + `search.results` event) and shows up to 5 results per section: Inbox, Tasks, Agents, Routines, Workflows, Instructions and Knowledge come from search settings declared on each kind in `document_kinds.DOCUMENT_KINDS` (every kind must declare one, or `None`), Tools and Schedules are searched directly, and Pages are matched in the browser against the sidebar navigation. A failing or slow section (2 s timeout) reports its own error without hiding the others.
- **Agent-maintained knowledge pages** (`/knowledge/<base-id>/`, `app/progenitor/knowledge/pages.py`, `app/progenitor/agent/knowledge_backend.py`, `app/backend/knowledge_pages.py`): each base in `/config/knowledge.json` is now a set of OKF v0.2 markdown pages stored as `knowledge:<base-id>/<path>.md` documents, written and maintained by agents. Concept pages need front matter with a `type` and get `generated.at` stamped on body changes; `index.md` is generated per directory, `log.md` is append-only. Pages are never deleted or moved: an obsolete page gets `status: deprecated`, which drops it from the generated indexes and the UI's file menus and ranks it below every current page in search, while it stays readable by path. Every version is kept as an immutable revision captured by a Postgres trigger (`knowledge_page_revisions`). Base ids are now kebab-case slugs (collisions get `-2`, …, and ids of removed bases that still have pages are avoided). Object agents get per-base access with `knowledge: [{base, access: read|write}]` (unknown bases are skipped with a warning in the result) and the new `curating-knowledge` skill. The Knowledge page replaces its Sources/Configure/Query tabs with a read-only page reader (breadcrumbs, sources, revision history, live updates). `managing-knowledge` now covers the catalog only.
- **Object agents can use MCP tools added at runtime** (`app/progenitor/agent/agent.py`): a manifest's `tools` list may name MCP tools (e.g. `jina_search_web`) discovered after startup; previously the agent failed with "requests unknown tools". Covered by the live `e2e/knowledge-research.spec.js` journey (Jina MCP → researcher fills a knowledge base → UI browsing and a deprecated page → read-only agent answers from the pages).
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

### Removed
- **In-process ACP runs and per-run ACP sandboxes** (`dispatch_tracked_acp_task`, `PROGENITOR_ACP_WORKSPACE`): ACP turns run only in the backend supervisor, in the task's `cwd` (else the agent's configured `cwd`). Stdio ACP agents now need their CLI in the backend image.
- **`app/progenitor/tools/gh.py`**: unused.
- **Workflow `notify` nodes and the `notify_user` agent tool**: workflows now create `alert` documents with `document_create`, optionally using a `format` node for the message; Progenitor raises alerts by writing `/alerts/<id>.json`.
- **Legacy pgvector knowledge stores**: the `knowledge_search` tool, folder sources and `index_folder`, the hash embedding (`progenitor-hash-256`), the `knowledge_documents` table (dropped on indexer start), the `POST /api/ui/knowledge/{base_id}/sources/{source_id}/index` and `GET /api/ui/knowledge/search` routes, and the catalog fields `status`, `embedding_model`, `dimensions`, `metric`, `index_type`, `connection_string`, and `sources` (dropped from stored bases on the next save). Superseded by the knowledge page index and `search` tool.
- **ACP through `call_agent`**: ACP agents are now started only with `acp_start`; `call_agent` rejects ACP transport and points callers to the dedicated tools.

### Changed
- **Repositories "+ New" opens Quick Chat**: it now opens Quick Chat with repository suggestions, like the other catalogue pages, instead of an in-page creation form. The agent declares the repository as `/config/repos/<id>.json`. The unused `createRepo` thunk and form styles are removed.
- **Frontend CSS cleanup**: removed unused selectors from ten global stylesheets, reducing global source CSS by 6,872 lines (26% by size). Preserved dynamic class families, light/dark themes, CSS Modules, dependency styles, keyframes and custom properties; no automatic purge step was added to the build.
- **Structured document renderer extracted**: the shared `StructuredDocumentBody` and `parseStructuredContent` own tagged Markdown section presentation with optional agent instruction and question actions. Inbox no longer exposes manual section writes; Tasks remain unchanged.
- **greetings_agent runs in its own container**: it gets a `requirements.txt`, `docker-entrypoint.sh` and manifest mounts, so the `agents` workflow builds `progenitor-agent-greetings_agent` (with the Copilot CLI for `probe_copilot`) and deploys it at `http://progenitor-agent-greetings_agent:8109`, replacing a hand-started container on the Progenitor image that never got updates or Azure credentials.
- **Inline inbox AI instructions** send through the inbox-edit quick-chat session without opening its pane. The popover shows Working until the response completes, then closes; inbox events independently show the latest document regardless of which writer caused the change. Failed requests remain retryable while the selected content is current; pop-out still opens an unsent quick-chat draft.
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
- **Instruction references are paths relative to `/instructions/`** (`workflows/card.md`), validated by one rule (`instruction_relative_path`) for the agent's `/instructions/` mount, the instructions API and workflow `instruction` fields: relative, no `..`, `.md` only.
- **Model profile names must be unique after trimming whitespace**; a catalog with `" general "` and `"general"` is rejected.

### Fixed
- **Frontend interaction races**: selecting an unchanged dropdown value preserves drafts, keyboard selection/dismissal restores trigger focus, and failed title saves keep the input focused. Chat title edits follow the selected conversation; schedule deep links survive cached-list refreshes; plugin completion survives snapshots arriving before acknowledgement. Lost detail streams settle loading with a retryable failure, and workflow supporting-data failures are reported instead of silently ignored.
- **Frontend design-library review regressions**: knowledge deletion reads the latest catalogue at confirmation; historical revision failures never display live content; workflow edge deletion and canonical missing-workflow route cleanup work again; schedule deep links wait for hydration. Rejected agent saves retain editable drafts, title editing isolates revisited entities from earlier saves, matching stream events recover failed keyed loads, reopened portal dropdowns stay in the viewport, and plugin/dashboard failure and completion messages reflect the correct operation.
- **Runtime session merges work on a single database connection**: `merge_session` (now the generic atomic `merge_document`) treated a plain connection as a pool and failed when no pool size was configured.
- **Production knowledge index**: the production Postgres runs `pgvector/pgvector:pg16` instead of `postgres:16-alpine`, which lacked the `vector` extension, so the knowledge indexer failed on every start.
- **Deploys catch up**: `build-and-deploy` compares `main` with the commit last deployed (recorded in the `progenitor-deploy-state` volume after the liveness checks pass) instead of only the current push, so a push whose tests failed, or that only touched CI files, no longer leaves production behind until someone runs a manual deploy. The Python images now rebuild for any change they copy in (all of `app/` except the frontend, `scripts/`, `data/schemas/`, …): the backend imports `app.progenitor` but used to rebuild only for `app/backend/` changes.
- **The Telegram "I just connected" greeting is sent once**: only a Telegram chat without a session document is greeted, instead of the same greeting being added to the chat on every backend restart.
- **Production deploy**: the Progenitor image builds again (`.dockerignore` still allowed `data/prompts/` after the Instructions rename, so `COPY data/instructions/` failed), and the runtime's `/app/repos` now lives at `/var/lib/progenitor/repos` instead of inside the runner checkout, where a clean checkout wiped it and its root-owned folder broke the agent builds.
- **CI on `main`**: the agents workflow reads each agent's runtime spec with a `docker run` step fed on stdin instead of the `mikefarah/yq` container action, whose output came back empty on the self-hosted runner and failed every image build; the build/deploy matrix holds only container agents (those with a `docker-entrypoint.sh`), so the in-process `greetings_agent` no longer fails the build; deploys no longer call the `POST /agents/register` route removed in July (agents keep their registry entries at stable container URLs). The gzip test accepts `Vary: Origin, Accept-Encoding`.
- Inbox AI edits no longer show the hand-edit conflict warning when Progenitor's document update arrives before its chat response finishes.
- An open manual inbox edit now follows live document updates instead of freezing the old message. Its unsaved draft remains visible but becomes read-only with Save disabled until the user closes it; the backend's 409 compare-and-swap still protects in-flight saves.
- `document_create` sets a schema-declared `timestamp` to the creation time (Unix seconds when the field is numeric), so alerts can be created from workflows.
- Cards created or deleted outside the card routes (workflow `document_create`/`document_delete`, extensions) now appear on and leave the Tasks board: the backend keeps `kanban_board.card_order` in step with `kanban_card:` documents from the event log.
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
