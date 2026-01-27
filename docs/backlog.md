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
Track iteration counts, verification scores, tool call latency. Expose `/metrics` endpoint for Prometheus.

### 20. Cancellation Tokens for Long-Running Tasks
Copilot CLI calls can timeout at 300s - should support user cancellation mid-build.

### 21. Streaming Responses in Chat Server
Current implementation waits for full response - could stream tokens for better UX.

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
