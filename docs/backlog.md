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

### 3. No Rate Limiting on Chat Server
**Status:** ✅ Completed
**Files:** `app/chat_server/main.py`, `app/chat_server/config.py`

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

### 14. Docker Health Check Only for Chat Server
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
**Files:** `app/progenitor/telemetry/` (new module), `app/progenitor/config.py`, `app/progenitor/runtime/loop.py`, `app/progenitor/runtime/agent_runner.py`, `app/chat_server/main.py`

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

### 21. Streaming Responses in Chat Server
**Status:** ✅ Completed
**Files:** `app/progenitor/llm/client.py`, `app/progenitor/runtime/loop.py`, `app/progenitor/main.py`, `app/chat_server/client.py`, `app/chat_server/config.py`, `.env.example`

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
| 2026-01-27 | #21 Streaming Responses in Chat Server | ✅ || 2026-01-27 | #22 Remove unused datetime import | ✅ |
| 2026-01-27 | #23 RateLimiter memory leak fix | ✅ |
| 2026-01-27 | #24 Extract shared env utils | ✅ |
| 2026-01-27 | #25 Add dict/object access helpers | ✅ |
| 2026-01-27 | #26 Enhanced health endpoint | ✅ |
| 2026-01-27 | #27 Configuration validation | ✅ |
| 2026-01-27 | #28 StateManager type hints | ✅ |
| 2026-01-27 | #29 Request ID propagation | ✅ |

---

## 🆕 2026-01-27 Code Review Findings

### 22. Unused Import in Chat Server Models
**Status:** ✅ Completed
**Files:** `app/chat_server/models.py`

Removed unused `datetime` import.

---

### 23. RateLimiter Memory Leak
**Status:** ✅ Completed
**Files:** `app/chat_server/main.py`

Added `cleanup_stale_clients()` method that removes client entries older than 1 hour. Called periodically during `is_allowed()` checks.

---

### 24. Duplicate Environment Helper Functions
**Status:** ✅ Completed
**Files:** `app/common/env_utils.py` (new), `app/chat_server/config.py`, `app/progenitor/config.py`

Extracted `_env_bool`, `_env_int`, `_env_float`, `_env_list` to shared `app/common/env_utils.py` module. Updated both config files to import from common module.

---

### 25. Inconsistent Object/Dict Handling
**Status:** ✅ Completed
**Files:** `app/common/utils.py` (new), `app/progenitor/graph_dual_loop.py`

Created `get_attr()` helper function for unified access to attributes from both objects and dicts. Updated graph routing functions to use helper.

---

### 26. Enhanced Health Endpoint
**Status:** ✅ Completed
**Files:** `app/chat_server/main.py`

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
**Files:** `app/chat_server/main.py`

Added middleware that:
- Reads `X-Request-ID` header if present
- Generates UUID if not present
- Adds to response headers
- Includes in all log entries for request

---

## 📋 Remaining Backlog Items

### Token Counting Accuracy
**Status:** 📋 Backlog
**Files:** `app/chat_server/main.py`

Uses naive word splitting for token counts. Consider using `tiktoken` for accurate counts.

### Configurable Shutdown Timeout
**Status:** 📋 Backlog
**Files:** `app/chat_server/client.py`

Hardcoded 5-second timeout. Make configurable.

### Backlog Size Limit
**Status:** 📋 Backlog
**Files:** `app/progenitor/state/manager.py`

Add configurable max size with oldest-item pruning.

### Dry-Run Mode
**Status:** 📋 Backlog

Add `PROGENITOR_DRY_RUN=true` mode that logs without executing LLM calls.