# Progenitor Improvement Backlog

Last updated: 2026-01-27

---

## 🔴 High Priority

### 1. Missing Error Boundaries in Graph Nodes
**Status:** ✅ Completed
**Files:** `app/progenitor/nodes/errors.py`, `app/progenitor/nodes/decide.py`

Created `NodeError` hierarchy with `RecoverableError`, `FatalError`, `LLMError`, etc. Updated `decide.py` with proper error classification and logging.

---

### 2. State Mutation in Routing Functions
**Status:** ✅ Completed
**Files:** `app/progenitor/graph_dual_loop.py`

Fixed `should_iterate()` to be a pure routing function that doesn't mutate state. State updates now happen only in nodes.

---

### 3. No Rate Limiting on Backend
**Status:** ✅ Completed
**Files:** `app/backend/main.py`, `app/backend/config.py`

Added in-memory rate limiting with configurable per-minute and per-hour limits. Includes `X-RateLimit-*` headers and proper 429 responses.

---

### 4. Checkpoint Saves Only Partial State
**Status:** ✅ Completed
**Files:** `app/progenitor/runtime/loop.py`

Fixed `_checkpoint_state()` to save full recoverable state including messages, backlog, ideal_state, and iteration progress.

---

## 🟠 Medium Priority

### 5. Duplicated Work Item Status Checking Logic
**Status:** ✅ Completed
**Files:** `app/progenitor/state/helpers.py`, `app/progenitor/nodes/decide.py`, `app/progenitor/graph_dual_loop.py`

Created `is_work_item_actionable()` and `get_actionable_items()` in `helpers.py`. Updated all callers to use the centralized utilities.

---

### 6. Hard-coded Failsafe Constants
**Status:** ✅ Completed
**Files:** `app/progenitor/graph_dual_loop.py`, `app/progenitor/config.py`

Added `max_inner_iterations`, `min_verification_score`, and `iteration_time_limit_seconds` to `RuntimeConfig`. Graph now uses configured values.

---

### 7. Missing Type Hints on `state: ProgenitorState`
**Status:** ⏳ Deferred
**Files:** `app/progenitor/state/models.py`

`ProgenitorState` is a `TypedDict` but `.get()` returns `Any`, losing type safety. This is a known limitation of TypedDict in Python.

**Note:** Already uses `total=False`. Direct attribute access or type narrowing is the recommended pattern. Full type safety would require a different architecture (Pydantic models vs TypedDict).

---

### 8. Verifier Uses LLM When LLM May Be Unavailable
**Status:** ✅ Completed
**Files:** `app/progenitor/state/verifier.py`

Added `heuristic_only` mode and `uses_llm` property. Verifier gracefully falls back to heuristic-only scoring when no LLM available.

---

### 9. No Worktree Cleanup on Failure
**Status:** ✅ Completed
**Files:** `app/progenitor/tools/git.py`

Added metadata tracking (`.progenitor_worktree` files), `cleanup_failed_worktree()` method, and automatic startup cleanup via `_startup_cleanup()`. Enhanced `cleanup_old_worktrees()` to use metadata timestamps.

---

### 10. Azure Temperature Adjustment is Surprising
**Status:** ✅ Completed
**Files:** `app/progenitor/llm/client.py`

Added debug logging when Azure provider adjusts temperature from user-specified value to 1.0.

---

## 🟡 Low Priority / Polish

### 11. Inconsistent Logging Formats
**Status:** ✅ Completed
**Files:** `app/progenitor/tools/registry.py`, `app/progenitor/tools/git.py`, `app/progenitor/tools/copilot.py`, `app/progenitor/runtime/loop.py`, `app/progenitor/runtime/agent_runner.py`, `app/progenitor/state/manager.py`

Converted f-string logs to lazy %-style formatting in key files.

---

### 12. `SECRET_PATTERNS` Regex Could Miss Variations
**Status:** ✅ Completed
**Files:** `app/progenitor/state/models.py`

Enhanced patterns to cover: OpenAI project keys, Azure keys, all GitHub token types (PAT, OAuth, server, user, fine-grained), and AWS access keys. Patterns ordered by specificity.

---

### 13. `__init__.py` Could Use Cleaner Exports
**Status:** ✅ Not Needed
**Files:** `app/progenitor/nodes/__init__.py`

Upon review, the exports already use a clean pattern with explicit `__all__` for IDE autocompletion.

---

### 14. Docker Health Check Only for Backend
**Status:** ✅ Completed
**Files:** `docker-compose.yml`, `scripts/healthcheck.py`, `app/progenitor/runtime/loop.py`

Added `health.json` file updated each cycle in loop.py. Created `healthcheck.py` script. Added health check to progenitor service in docker-compose.

---

### 15. Test Coverage Gaps
**Status:** ✅ Improved
**Files:** `tests/progenitor/test_e2e_dual_loop.py`

Added end-to-end integration tests for dual-loop cycle including graph creation, state initialization, work item transitions, iteration control, and verification stopping conditions. 132 tests now passing.

---

### 16. Missing `.env.example`
**Status:** ✅ Completed
**Files:** `.env.example`

Updated `.env.example` with comprehensive documentation for all environment variables, organized by category with visual groupings.

---

### 17. Retry Logic Doesn't Jitter
**Status:** ✅ Completed
**Files:** `app/progenitor/llm/client.py`

Added ±50% random jitter to retry delays to prevent thundering herd when multiple instances hit rate limits simultaneously.

---

### 18. No Structured Logging (JSON)
**Status:** ✅ Completed
**Files:** `app/progenitor/config.py`, `.env.example`

Added `log_format` config option ("text" or "json"). JSON format outputs structured logs with timestamp, level, logger, message, and optional fields for log aggregation tools.

---

## 💡 Feature Ideas

### 19. Add Metrics / Observability
**Status:** ✅ Completed
**Files:** `app/progenitor/telemetry/` (new module), `app/progenitor/config.py`, `app/progenitor/runtime/loop.py`, `app/progenitor/runtime/agent_runner.py`, `app/backend/main.py`

Implemented OpenTelemetry-style metrics and tracing:
- **Metrics**: Counter, Gauge, Histogram with Prometheus-compatible export
- **Tracing**: Spans with parent-child relationships, W3C Trace Context propagation
- **Export**: JSONL files (traces.jsonl, metrics.jsonl) for log aggregation
- **Instrumentation**: Decorators for nodes, tools, LLM calls; helpers for iterations and agents
- **/metrics endpoint**: Prometheus text format + JSON format at `/metrics/json`
- **Sub-agent tracking**: Context propagation to child agents with `AgentTelemetry`
- **Configuration**: `TelemetryConfig` with env vars (PROGENITOR_TELEMETRY_*)

Tracked metrics include: cycle counts, iteration counts/duration, verification scores, tool call latency, LLM token usage, agent spawn/complete/fail counts, node execution timing.

---

### 20. Cancellation Tokens for Long-Running Tasks
Copilot CLI calls can timeout at 300s - should support user cancellation mid-build.

### 21. Streaming Responses in Backend
**Status:** ✅ Completed
**Files:** `app/progenitor/llm/client.py`, `app/progenitor/runtime/loop.py`, `app/progenitor/main.py`, `app/backend/client.py`, `app/backend/config.py`, `.env.example`

Implemented real-time token streaming for better UX:
- **LLM Client**: Added `chat_stream()` async generator method that yields tokens from the OpenAI streaming API
- **Loop Runner**: Added streaming queue (`_stream_queue`) and `get_stream_chunk()` method for real-time chunk delivery
- **Progenitor Main**: Added `_stream_writer()` task that writes streaming chunks to stdout as JSON lines
- **ProgenitorClient**: Updated `send_message_stream()` to handle real streaming chunks from subprocess
- **Configuration**: Added `CHAT_SERVER_STREAM_ENABLED` and `CHAT_SERVER_STREAM_CHUNK_DELAY_MS` env vars
- **Protocol**: Stream chunks use `{"type": "stream_chunk", "content": "...", "is_final": bool}` format
- **Backwards Compatible**: Falls back to simulated streaming (word-by-word) for legacy responses

---

## Changelog

| Date | Item | Status |
|------|------|--------|
| 2026-01-27 | Initial backlog created | ✅ |
| 2026-01-27 | #1 Error boundaries added | ✅ |
| 2026-01-27 | #2 State mutation fixed | ✅ |
| 2026-01-27 | #3 Rate limiting added | ✅ |
| 2026-01-27 | #4 Full checkpoint implemented | ✅ |
| 2026-01-27 | #5 Consolidated _is_actionable logic | ✅ |
| 2026-01-27 | #6 Config for magic numbers | ✅ |
| 2026-01-27 | #7 TypedDict type hints | ⏸️ Deferred |
| 2026-01-27 | #8 Verifier heuristic fallback | ✅ |
| 2026-01-27 | #9 Worktree cleanup on failure | ✅ |
| 2026-01-27 | #10 Azure temperature warning | ✅ |
| 2026-01-27 | #11 Consistent lazy logging | ✅ |
| 2026-01-27 | #12 Enhanced SECRET_PATTERNS | ✅ |
| 2026-01-27 | #13 __init__.py exports | ✅ Not Needed |
| 2026-01-27 | #14 Docker health check for progenitor | ✅ |
| 2026-01-27 | #15 E2E integration tests | ✅ |
| 2026-01-27 | #16 .env.example updated | ✅ |
| 2026-01-27 | #17 Retry jitter added | ✅ |
| 2026-01-27 | #18 JSON structured logging | ✅ |
| 2026-01-27 | #19 Metrics / Observability (OTLP) | ✅ |
| 2026-01-27 | #21 Streaming Responses in Backend | ✅ || 2026-01-27 | #22 Remove unused datetime import | ✅ |
| 2026-01-27 | #23 RateLimiter memory leak fix | ✅ |
| 2026-01-27 | #24 Extract shared env utils | ✅ |
| 2026-01-27 | #25 Add dict/object access helpers | ✅ |
| 2026-01-27 | #26 Enhanced health endpoint | ✅ |
| 2026-01-27 | #27 Configuration validation | ✅ |
| 2026-01-27 | #28 StateManager type hints | ✅ |
| 2026-01-27 | #29 Request ID propagation | ✅ |
| 2026-01-27 | Sprint 5: Chat UI Advanced Features | ✅ |

---

## 🖥️ Chat UI Sprint 5 - Advanced Features

### 30. Conversation Sessions
**Status:** ✅ Completed
**Files:** `app/frontend/src/sessions.js`, `app/frontend/src/styles/status.css`

Multi-conversation session management:
- Create, rename, delete sessions
- Switch between sessions with full message history
- Session persistence in localStorage
- Auto-create new sessions
- Message counts per session

---

### 31. Syntax Highlighting
**Status:** ✅ Completed
**Files:** `app/frontend/src/syntax.js`, `app/frontend/src/styles/chat.css`

Lightweight syntax highlighting without external dependencies:
- Support for 12+ languages (JS, TS, Python, JSON, HTML, CSS, SQL, Bash, YAML, etc.)
- Language auto-detection from code fence
- Color themes for dark/light modes
- Keyword, string, comment, number highlighting

---

### 32. Prompt Templates
**Status:** ✅ Completed
**Files:** `app/frontend/src/templates.js`, `app/frontend/src/styles/chat.css`

Quick-access prompt templates:
- 12 built-in templates (system queries, coding tasks, general)
- Category filtering (system, coding, general, custom)
- Placeholder support for user input
- Custom template creation and storage
- Template search functionality

---

### 33. Context Menu
**Status:** ✅ Completed
**Files:** `app/frontend/src/context-menu.js`, `app/frontend/src/styles/chat.css`

Right-click context menu for messages:
- Copy message content
- Copy code blocks
- Edit user messages
- Regenerate assistant responses
- Select all text
- Quote message in reply
- Delete messages

---

### 34. Message Actions
**Status:** ✅ Completed
**Files:** `app/frontend/src/chat.js`, `app/frontend/src/styles/chat.css`

Message action buttons:
- Copy button with visual feedback
- Regenerate response button
- Message timestamps
- Message IDs for referencing

---

### 35. Metrics Dashboard
**Status:** ✅ Completed
**Files:** `app/frontend/src/metrics.js`, `app/frontend/src/styles/status.css`

Real-time metrics visualization:
- Messages sent/received counters
- Average response time tracking
- Response time chart (last 20 responses)
- Session count
- Error count with last error details
- Uptime tracking
- Metrics persistence

---

### 36. Logs Viewer
**Status:** ✅ Completed
**Files:** `app/frontend/src/logs-viewer.js`, `app/frontend/src/styles/status.css`

Real-time agent logs viewer:
- Level filtering (debug, info, warn, error)
- Text search
- Auto-scroll toggle
- Expandable log data
- Color-coded log levels
- Clear/refresh functionality

---

### 37. Draft Auto-Save
**Status:** ✅ Completed
**Files:** `app/frontend/src/drafts.js`

Auto-save message drafts:
- Debounced saving (500ms)
- Session-aware drafts
- Draft indicator in UI
- Automatic restoration
- Manual clear option

---

### 38. Accessibility Features
**Status:** ✅ Completed
**Files:** `app/frontend/src/accessibility.js`

Comprehensive accessibility support:
- ARIA landmarks and labels
- Skip links for keyboard navigation
- Screen reader announcements (live regions)
- Focus trap for modals
- Keyboard navigation in message list
- Reduced motion preference support
- High contrast mode support
- Focus visible indicators

---

### 39. Export/Import Sessions
**Status:** ✅ Completed
**Files:** `app/frontend/src/export-import.js`

Session export and import:
- Export current session
- Export all sessions
- Multiple formats: JSON, Markdown, Plain Text
- Import from JSON files
- Keyboard shortcut (Ctrl+Shift+E)

---

### 40. Performance Optimizations
**Status:** ✅ Completed
**Files:** `app/frontend/src/performance.js`

Performance monitoring and optimization:
- Performance observer for metrics
- Lazy loading for images
- Virtual scrolling for large message lists
- DOM operation batching
- Memory management (auto-cleanup)
- Content visibility optimization
- Debounce/throttle utilities

---

### 41. File Attachments
**Status:** ✅ Completed
**Files:** `app/frontend/src/attachments.js`

File upload and attachment support:
- Click to upload
- Drag and drop
- Paste images from clipboard
- File type validation
- Size limits (10MB)
- Image previews
- Multiple file support
- Attachment removal

---

### 42. Typing Indicator
**Status:** ✅ Completed
**Files:** `app/frontend/src/typing-indicator.js`

Assistant typing indicator:
- Animated typing dots
- Customizable status text
- Auto-scroll on show
- Screen reader announcements

---

### 43. Message Reactions
**Status:** ✅ Completed
**Files:** `app/frontend/src/reactions.js`

Emoji reactions on messages:
- 8 available reactions
- Reaction picker popup
- Toggle reactions on/off
- Reaction counts
- Reactions persistence
- Hover to show add button

---

## 📋 Remaining Backlog Items

### 22. Unused Import in Backend Models
**Status:** ✅ Completed
**Files:** `app/backend/models.py`

Removed unused `datetime` import.

---

### 23. RateLimiter Memory Leak
**Status:** ✅ Completed
**Files:** `app/backend/main.py`

Added `cleanup_stale_clients()` method that removes client entries older than 1 hour. Called periodically during `is_allowed()` checks.

---

### 24. Duplicate Environment Helper Functions
**Status:** ✅ Completed
**Files:** `app/common/env_utils.py` (new), `app/backend/config.py`, `app/progenitor/config.py`

Extracted `_env_bool`, `_env_int`, `_env_float`, `_env_list` to shared `app/common/env_utils.py` module. Updated both config files to import from common module.

---

### 25. Inconsistent Object/Dict Handling
**Status:** ✅ Completed
**Files:** `app/common/utils.py` (new), `app/progenitor/graph_dual_loop.py`

Created `get_attr()` helper function for unified access to attributes from both objects and dicts. Updated graph routing functions to use helper.

---

### 26. Enhanced Health Endpoint
**Status:** ✅ Completed
**Files:** `app/backend/main.py`

Added to health response: `uptime_seconds`, `last_activity`, `rate_limit_status`, and `version`.

---

### 27. Configuration Validation
**Status:** ✅ Completed
**Files:** `app/progenitor/config.py`

Added `validate()` method to `ProgenitorConfig` that checks:
- Azure provider requires endpoint AND deployment
- OpenAI provider requires API key
- Storage directories are valid paths

---

### 28. StateManager Type Hints
**Status:** ✅ Completed
**Files:** `app/progenitor/state/manager.py`

Added comprehensive return type hints to all public methods.

---

### 29. Request ID Propagation
**Status:** ✅ Completed
**Files:** `app/backend/main.py`

Added middleware that:
- Reads `X-Request-ID` header if present
- Generates UUID if not present
- Adds to response headers
- Includes in all log entries for request

---

## 📋 Remaining Backlog Items

### Token Counting Accuracy
**Status:** ✅ Completed (Sprint 6)
**Files:** `app/progenitor/llm/tokenizer.py`

Added tiktoken integration for accurate OpenAI token counting. Created new tokenizer module with `count_tokens()`, `count_message_tokens()`, and `truncate_to_tokens()` functions. Falls back to estimation when tiktoken unavailable.

### Configurable Shutdown Timeout
**Status:** ✅ Completed (Sprint 4)
**Files:** `app/backend/config.py`, `app/backend/client.py`

Added `progenitor_stop_timeout` config option with `CHAT_SERVER_PROGENITOR_STOP_TIMEOUT` env var.

### Backlog Size Limit
**Status:** 📋 Backlog
**Files:** `app/progenitor/state/manager.py`

Add configurable max size with oldest-item pruning.

### Dry-Run Mode
**Status:** ✅ Completed (Sprint 6)
**Files:** `app/progenitor/llm/mock_client.py`

Added `PROGENITOR_MOCK_LLM=true` mode that returns simulated responses without API calls. Includes `MockLLMClient` class and `create_llm_client()` factory function.

---

## 🏃 Sprint 6 Summary (2026-01-29)

### Completed Items
1. ✅ **Tiktoken Integration** - Accurate token counting with `app/progenitor/llm/tokenizer.py`
2. ✅ **Mock LLM Client** - Demo mode with `PROGENITOR_MOCK_LLM=true` environment variable
3. ✅ **CHANGELOG.md** - Keep a Changelog format with version history
4. ✅ **Pre-commit Hooks** - `.pre-commit-config.yaml` with ruff, mypy, bandit
5. ✅ **pyproject.toml** - Modern Python packaging with tool configs
6. ✅ **CQ-009 Fix** - Added DEBUG logging to silent exception handlers
7. ✅ **CQ-011 Verified** - Confirmed configurable stop timeout already implemented

### New Tests Added
- `test_tokenizer.py` - 44 tests for tokenizer module
- `test_mock_llm_client.py` - 30 tests for mock LLM client

### Total Tests: 540 passed, 2 skipped