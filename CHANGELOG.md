# Changelog

All notable changes to the Progenitor project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Instructions** (renamed from Prompts; `data/instructions/`, `/instructions/` mount, `instruction:` documents, `/api/ui/instructions`, `app/backend/instruction_service.py`, `InstructionsPage.jsx`): every instruction now starts with YAML front matter (`title`, `description`, and `variables` for the `{placeholders}` the code fills in), which loaders strip before sending the body. The page sits in the sidebar under Delegate, matches the other list/detail pages, and links each instruction at `/instructions/<group>/<file>.md`. Only `progenitor/` instructions are read by the runtime, so only they are editable (front matter is validated and `variables` cannot change); edits to `progenitor/genesis.md` apply on the next model call. Other groups are bundled with their A2A agent, which reads its own files, and show as read-only. Every version is kept in the new immutable `instruction_revisions` table and can be viewed and restored. The runtime syncs `data/instructions/` on every boot instead of seeding once. Agent discovery's missing user instruction is restored (`progenitor/agent_discovery_analysis.md`), `decide.md` moved to `builder_agent/` (its only reader), the unused `progenitor/system.md` and `scheduled_prefix.md` were removed, and `PROGENITOR_PROMPTS_DIR` is now `PROGENITOR_INSTRUCTIONS_DIR`.
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
- **Legacy pgvector knowledge stores**: the `knowledge_search` tool, folder sources and `index_folder`, the hash embedding (`progenitor-hash-256`), the `knowledge_documents` table (dropped on indexer start), the `POST /api/ui/knowledge/{base_id}/sources/{source_id}/index` and `GET /api/ui/knowledge/search` routes, and the catalog fields `status`, `embedding_model`, `dimensions`, `metric`, `index_type`, `connection_string`, and `sources` (dropped from stored bases on the next save). Superseded by the knowledge page index and `search` tool.

### Changed
- **Chat history reads go through the runtime** (`app/backend/runtime_conversations.py`): the backend no longer rebuilds messages from checkpoint blobs; it pages the runtime's conversation items (cached per checkpoint, pooled connections, off the event loop). Fixes chats rendering as an empty "New chat" when their latest checkpoint held a `DeltaChannel` snapshot. While the runtime is unreachable, chats keep their last known history, or list as "history unavailable" with a 503 on open, instead of looking empty.
- **Backend ↔ runtime chat transport**: `SSEProgenitorClient` uses `/v1/responses`; `/v1/chat` and `/v1/chat/{id}/cancel` are removed. A request without a `conversation` now runs in a fresh `conv_` conversation instead of the runtime's main thread; Telegram turns use a per-chat `tele_` conversation. Deleting a chat in the UI now also deletes its runtime thread and live state, and a deleted chat no longer reappears as "Current chat".
- **Tool results in conversation items**: `function_call_output.output` keeps multi-part tool results as Responses input parts (text, images) instead of a Python repr.
- **Stopping a reply keeps it**: when a turn is cancelled, the runtime saves the text streamed so far to the conversation (native `aupdate_state`, marked cancelled). After a reload the chat shows `… [Cancelled]` instead of losing the reply, and the next turn sees it, so "continue" picks up where it stopped.
- **`langgraph-checkpoint-postgres` pinned to the langgraph#8556 fix commit** (`pyproject.toml`): reading a checkpoint more than 1,024 checkpoints back in a thread (DeltaChannel messages) silently returned no messages on 3.1.2 (langgraph#8448). No PyPI release has the fix yet; revert to a version pin once one does. Existing virtualenvs need `pip install --force-reinstall --no-deps` of the pinned URL, since the commit still reports version 3.1.2.
- **Schedule run results** read run threads with the agent graph's `aget_state` instead of the backend document store.
- **E2E routine fixtures are self-contained**: `scripts/seed_e2e_fixtures.py` is removed. The connector, editing and sequences-redirect specs insert their own uniquely-suffixed copies of the graphs in `app/frontend/e2e/fixtures/routine-graphs.json` (`seedGraphFixtures` in `e2e/helpers/routineFixture.mjs`) and delete them afterwards, so the suite needs no seed step and leaves nothing behind. `tests/backend/test_e2e_routine_fixtures.py` keeps the graphs valid.
- **Conversation Compressor**: Now uses tiktoken for accurate token estimation
- **Exception Handling**: Added DEBUG logging to previously silent exception handlers (CQ-009)

### Fixed
- `/config/mcp_tools.json` rejected nothing outside `tools`: an agent edit that added its own top-level key (e.g. a `tool_overrides` map) was silently dropped and reported as success while no tool changed. Any change outside a tool's `enabled` flag is now rejected with a message pointing at the flag.
- `grep` and `glob` on the agent's virtual folders (`/config`, `/tasks`, `/inbox`, `/workflows`, `/routines`, `/policies`, content documents) always returned nothing, so the agent was told text it had just read didn't exist (this is how it ended up inventing a `tool_overrides` key). They now search the documents as `read_file` renders them, with matching line numbers.
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
