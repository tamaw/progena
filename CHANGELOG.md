# Changelog

All notable changes to the Progenitor project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Chat history mount for the agent** (`/conversations/`, `app/progenitor/conversations/`, `app/progenitor/agent/conversation_backend.py`): every non-archived chat is a read-only `<date>_<title-slug>_<id8>.txt` in IRC style — `<nick>` lines for what was said, `* nick` actions for reasoning titles and tool calls (failures read `can't …`), `-nick-` notices, join/part plus `-> *agent*` for subagent, async, A2A, and direct `@agent` turns. The user's nick and timezone come from account preferences; titles use the chat sidebar's rule (now shared in `conversations/sessions.py`). Logs are rendered from the LangGraph checkpointer and cached in the store (`progenitor.conversations`): `ls` and `grep` use the cache, `read_file` re-renders a chat whose newest checkpoint changed. The `reading-conversations` skill documents the format.
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
