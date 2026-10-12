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
- **Workflow canvas**: the cursor shows grab while the right button is held to pan
- **Model profiles**: Added a separate searchable list/detail page at `/profiles`, backed by `/config/profiles.json`. Users can edit deployment and reasoning overrides in the same bordered configuration surface as agents, delete a profile from the detail header, restore environment inheritance, and receive live Postgres-driven updates in both the page and routine profile selectors. Progenitor can add, edit, or remove complete profile maps through its agent-writable virtual file. Drafts are preserved and saves blocked when the selected profile changes remotely.
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
- **Conversation Compressor**: Now uses tiktoken for accurate token estimation
- **Exception Handling**: Added DEBUG logging to previously silent exception handlers (CQ-009)

### Fixed
- Profile notifications now bypass unrelated state-snapshot diffs; profile queries and edits use `202` acknowledgements plus correlated `profiles.snapshot` events instead of successful HTTP response-body data.
- Snapshot-confirmed saves no longer have their success status overwritten by the acknowledgement-waiting effect; request failures preserve the draft and show the error.
- Schedule and inbox document notifications now reach their collection subscribers even when a sibling document from the same transaction has already refreshed the shared state snapshot.
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
