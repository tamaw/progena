# Progenitor Comprehensive Improvement Backlog

> Generated: 2026-01-27
> Updated: 2026-01-27
> Comprehensive code review covering architecture, security, reliability, and code quality.

---

## Completion Status

| ID | Item | Status | Tests |
|----|------|--------|-------|
| SEC-001 | Command Injection Fix | ✅ DONE | 20 tests |
| SEC-002 | Secret Redaction Pattern | ✅ DONE | 26 tests |
| REL-001 | LLM Response Validation | ✅ DONE | 28 tests |
| REL-002 | Graceful Process Termination | ✅ DONE | 8 tests |
| REL-003 | Bounded Input Queue | ✅ DONE | 15 tests |
| REL-004 | Rate Limiter Memory | ✅ DONE | 11 tests |
| ARCH-001 | Composable State Types | ✅ DONE | 13 tests |
| ARCH-002 | Dependency Injection | ✅ DONE | 16 tests |
| ARCH-003 | Telemetry Decorator | ✅ DONE | 17 tests |
| CQ-001 | Replace Bare Exception Handlers | ✅ DONE | 12 tests |
| CQ-002 | Config Validation at Startup | ✅ DONE | 21 tests |
| OBS-001 | Request ID Correlation | ✅ DONE | 11 tests |
| CQ-003 | Use Async File I/O Consistently | ✅ DONE | 14 tests |
| CQ-004 | Remove Unused Imports | ✅ DONE | 0 tests |
| CQ-005 | Extract Magic Numbers to Config | ✅ DONE | 0 tests |
| OBS-002 | Unified Observability Initialization | ✅ DONE | 0 tests |
| OPS-003 | Docker Health Check | ✅ DONE | 0 tests |
| OPS-004 | Resource Limits in docker-compose | ✅ DONE | 0 tests |

**Total: 18 items completed with 212 new tests**

---

## Executive Summary

The Progenitor codebase demonstrates strong foundational architecture with a sophisticated dual-loop LangGraph implementation. This backlog identifies **47 actionable improvements** across 8 categories, prioritized by impact and effort.

**Key Strengths:**
- Well-designed dual-loop architecture (OBSERVE → THINK → PLAN → BUILD → EXECUTE → VERIFY → LEARN)
- Clean separation between outer loop (orchestration) and inner loop (iterative refinement)
- Proper atomic file writes and cross-platform file locking
- Good secret redaction patterns
- LLM retry logic with exponential backoff and jitter

**Key Concerns:**
- Security vulnerabilities in command handling
- State management complexity (40+ fields in ProgenitorState)
- Scattered telemetry boilerplate across all nodes
- Subprocess-based IPC between backend and progenitor (fragile)
- Missing dependency injection patterns

---

## 🔴 Priority 1: Critical Security & Reliability

### SEC-001: Command Injection Risk in MCP Client
**File:** `app/progenitor/tools/mcp.py`
**Issue:** Command strings are split naively with `command.split()` without proper sanitization.
**Risk:** If commands originate from external input, this enables command injection.
**Fix:** Use `shlex.split()` for proper shell escaping, validate against allowlist.
```python
# Before
cmd_parts = command.split()

# After
import shlex
cmd_parts = shlex.split(command)
# Plus: validate against allowed command patterns
```
**Effort:** Low | **Impact:** Critical

---

### SEC-002: Overly Broad Secret Redaction Pattern
**File:** `app/progenitor/state/models.py`
**Issue:** Pattern `r'[A-Za-z0-9]{32}'` matches ANY 32-char alphanumeric string including UUIDs, hashes, work item IDs.
**Risk:** Over-redaction causes data loss in logs, makes debugging impossible.
**Fix:** Make pattern more specific (require Azure key prefix patterns).
```python
# Before
(r'[A-Za-z0-9]{32}', '[AZURE_KEY_REDACTED]'),

# After - Only match actual Azure key patterns
(r'(?<![A-Za-z0-9])[A-Fa-f0-9]{32}(?![A-Za-z0-9])', '[AZURE_KEY_REDACTED]'),
```
**Effort:** Low | **Impact:** High

---

### REL-001: Missing Input Validation on LLM Responses
**Files:** `nodes/decide.py`, `nodes/planner.py`, `llm/client.py`
**Issue:** LLM responses are parsed as JSON without schema validation. Malformed responses propagate silently.
**Fix:** Add Pydantic models for all LLM response schemas.
```python
class DecisionResponse(BaseModel):
    action: DecisionAction
    reasoning: str
    requires_planning: bool = False
    direct_response: Optional[str] = None

# In decide.py
decision_data = DecisionResponse.model_validate(raw_response)
```
**Effort:** Medium | **Impact:** High

---

### REL-002: Subprocess Timeout Without Graceful Termination
**File:** `app/progenitor/tools/copilot.py`
**Issue:** On timeout, processes are immediately `kill()`ed without `terminate()` first.
**Risk:** Orphaned processes, corrupted state files, resource leaks.
**Fix:** Implement graceful shutdown sequence.
```python
# After
except asyncio.TimeoutError:
    if process is not None:
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            process.kill()
```
**Effort:** Low | **Impact:** High

---

### REL-003: Unbounded Input Queue
**File:** `app/progenitor/runtime/loop.py`
**Issue:** `asyncio.Queue()` has no `maxsize`, can grow unbounded under load.
**Risk:** Memory exhaustion under sustained load.
**Fix:** Add bounded queue with backpressure handling.
```python
self._input_queue: asyncio.Queue = asyncio.Queue(maxsize=100)

async def enqueue_input(self, msg: str) -> bool:
    try:
        self._input_queue.put_nowait(msg)
        return True
    except asyncio.QueueFull:
        logger.warning("Input queue full, applying backpressure")
        return False
```
**Effort:** Low | **Impact:** Medium

---

### REL-004: Rate Limiter Memory Growth
**File:** `app/backend/main.py`
**Issue:** Rate limiter dictionaries can grow unbounded with many unique client IPs (DDoS scenario).
**Fix:** Add LRU eviction with maximum client count.
```python
from collections import OrderedDict

class BoundedRateLimiter:
    MAX_CLIENTS = 10000

    def record_request(self, client_id: str):
        if len(self._clients) >= self.MAX_CLIENTS:
            self._clients.popitem(last=False)  # Remove oldest
```
**Effort:** Low | **Impact:** Medium

---

## 🟡 Priority 2: Architecture & Maintainability

### ARCH-001: Split ProgenitorState into Composable Substates
**File:** `app/progenitor/state/models.py`
**Issue:** `ProgenitorState` TypedDict has 40+ fields, mixing conversation, inner loop, and orchestration state.
**Impact:** Hard to reason about state transitions, prone to bugs, difficult to test.
**Fix:** Create composable substates.
```python
class ConversationState(TypedDict):
    messages: list[ConversationMessage]
    current_input: Optional[str]
    pending_response: Optional[str]

class InnerLoopState(TypedDict):
    ideal_state: Optional[IdealState]
    current_iteration: int
    verification_score: float
    artefacts: list[dict]

class OrchestrationState(TypedDict):
    backlog: list[WorkItem]
    current_work_item: Optional[WorkItem]
    shutdown_requested: bool

class ProgenitorState(ConversationState, InnerLoopState, OrchestrationState):
    """Composed from focused substates"""
    pass
```
**Effort:** High | **Impact:** High

---

### ARCH-002: Implement Dependency Injection Container
**Files:** All node files, `graph_dual_loop.py`
**Issue:** Dependencies are manually wired through 14+ node factories with inconsistent signatures.
**Impact:** Testing friction, hard to mock, violates DRY.
**Fix:** Create dependency container.
```python
@dataclass
class NodeDependencies:
    llm_client: LLMClientProtocol
    state_manager: StateManagerProtocol
    tool_registry: ToolRegistry
    config: ProgenitorConfig
    worktrees_dir: Path

def create_all_nodes(deps: NodeDependencies) -> dict[str, Callable]:
    """Single function to create all nodes with shared dependencies"""
    return {
        "decide": decide.create_node(deps),
        "observe": observe.create_node(deps),
        # ...
    }
```
**Effort:** Medium | **Impact:** High

---

### ARCH-003: Extract Telemetry as Cross-Cutting Concern
**Files:** All 19 node files
**Issue:** Every node has identical `_get_telemetry()` function and manual span management.
**Impact:** 400+ lines of duplicated boilerplate, inconsistent telemetry coverage.
**Fix:** Create decorator for automatic instrumentation.
```python
def traced_node(node_name: str):
    def decorator(create_fn):
        def wrapper(*args, **kwargs):
            original_node = create_fn(*args, **kwargs)

            async def instrumented_node(state: ProgenitorState):
                tracer, metrics = get_telemetry()
                with tracer.start_span(f"node_{node_name}") if tracer else nullcontext():
                    start = time.perf_counter()
                    result = await original_node(state)
                    if metrics:
                        metrics.record_histogram("node_duration", time.perf_counter() - start)
                    return result
            return instrumented_node
        return wrapper
    return decorator
```
**Effort:** Medium | **Impact:** Medium

---

### ARCH-004: Replace Subprocess IPC with Direct Async
**Files:** `app/backend/client.py`, `app/backend/main.py`
**Issue:** Backend spawns Progenitor as subprocess, communicates via stdin/stdout pipes.
**Risk:** Deadlocks, parsing errors, zombie processes, no structured error handling.
**Fix:** Run Progenitor as in-process async service or use message queue (Redis streams).
```python
class ProgenitorService:
    """In-process Progenitor service"""
    async def handle_message(self, msg: str) -> str:
        # Direct async call to graph
        result = await self.graph.ainvoke({"current_input": msg})
        return result.get("pending_response", "")

class ProgenitorClient:
    def __init__(self, service: ProgenitorService):
        self._service = service

    async def send_message(self, msg: str) -> str:
        return await self._service.handle_message(msg)
```
**Effort:** High | **Impact:** High

---

### ARCH-005: Define Protocol Interfaces for Dependencies
**Files:** All tool and service files
**Issue:** Nodes depend on concrete implementations, not interfaces.
**Impact:** Tight coupling, difficult to mock for testing.
**Fix:** Define protocols.
```python
from typing import Protocol

class LLMClientProtocol(Protocol):
    async def generate(self, prompt: str, system_prompt: str = "") -> str: ...
    async def decide(self, user_input: str, ...) -> dict: ...
    async def stream(self, prompt: str, ...) -> AsyncIterator[str]: ...

class StateManagerProtocol(Protocol):
    async def log_decision(self, decision: dict) -> None: ...
    async def commit_state(self) -> None: ...
```
**Effort:** Medium | **Impact:** Medium

---

### ARCH-006: Split StateManager Responsibilities
**File:** `app/progenitor/state/manager.py`
**Issue:** StateManager handles persistence, logging, AND business logic (backlog management).
**Impact:** 427 lines, hard to test, violates single responsibility.
**Fix:** Split into focused classes.
```python
class StatePersistence:
    """Only handles reading/writing JSON with atomic ops"""
    async def read(self, name: str) -> dict: ...
    async def write(self, name: str, data: dict) -> None: ...

class BacklogManager:
    """Business logic for work items"""
    def get_actionable_items(self) -> list[WorkItem]: ...
    def add_item(self, item: WorkItem) -> None: ...

class StateLogger:
    """JSONL logging with redaction"""
    async def log(self, category: str, entry: dict) -> None: ...
```
**Effort:** High | **Impact:** Medium

---

## 🟢 Priority 3: Code Quality & Best Practices

### CQ-001: Replace Bare Exception Handlers
**Files:** Multiple (see list below)
**Issue:** Bare `except` clauses suppress errors without logging.

| File | Location | Issue |
|------|----------|-------|
| `tools/copilot.py` | healthcheck | Silently swallows failures |
| `tools/registry.py` | metadata read | Silently swallows failures |
| `tools/git.py` | worktree removal | Silently swallows failures |
| `runtime/loop.py` | cleanup | Ignores cleanup errors |

**Fix:** Log exceptions, use specific exception types.
```python
# Before
except Exception:
    pass

# After
except (IOError, OSError) as e:
    logger.warning("Failed to clean up worktree: %s", e)
```
**Effort:** Low | **Impact:** Medium

---

### CQ-002: Add Config Validation at Startup
**Files:** `app/progenitor/config.py`, `app/backend/config.py`
**Issue:** Invalid config values (negative timeouts, invalid paths) aren't caught until runtime.
**Fix:** Add Pydantic validation.
```python
from pydantic import BaseModel, PositiveInt, field_validator

class RuntimeConfig(BaseModel):
    max_inner_iterations: PositiveInt = 5
    min_verification_score: float = 0.8

    @field_validator('min_verification_score')
    @classmethod
    def validate_score(cls, v):
        if not 0 <= v <= 1:
            raise ValueError('Score must be between 0 and 1')
        return v
```
**Effort:** Medium | **Impact:** Medium

---

### CQ-003: Use Async File I/O Consistently
**File:** `app/progenitor/state/manager.py`
**Issue:** Mix of sync (`open()`) and async patterns. Sync I/O blocks event loop.
**Fix:** Use `aiofiles` for all file operations.
```python
import aiofiles

async def _read_json(self, filepath: Path) -> Any:
    async with aiofiles.open(filepath, 'r') as f:
        content = await f.read()
        return json.loads(content)
```
**Effort:** Medium | **Impact:** Low

---

### CQ-004: Remove Unused Imports
**Files:**
- `tools/copilot.py` - `subprocess` imported but uses `asyncio.subprocess`
- `tools/mcp.py` - `subprocess` imported but not used

**Effort:** Low | **Impact:** Low

---

### CQ-005: Extract Magic Numbers to Config
**Files:** Multiple

| File | Value | Description |
|------|-------|-------------|
| `backend/main.py` | `300` | Cleanup interval |
| `backend/main.py` | `3600` | Stale threshold |
| `runtime/loop.py` | `120` | Health stale seconds |
| `graph_dual_loop.py` | `5` | Default max iterations |

**Fix:** Move all magic numbers to configuration.
**Effort:** Low | **Impact:** Low

---

### CQ-006: Address Type Ignore Comments
**Files:** Multiple

| File | Comment | Action |
|------|---------|--------|
| `observability/__init__.py` | `# type: ignore[import-untyped]` | Add type stubs or inline types |
| `main.py` | Type mismatch | Fix return type annotation |
| `backend/main.py` | Late initialization | Use `Optional` or sentinel |

**Effort:** Medium | **Impact:** Low

---

### CQ-007: Standardize Error Handling Pattern
**Issue:** Codebase mixes: custom exceptions, raw Exception, silent suppression.
**Fix:** Document and enforce error handling policy.
```python
# Policy:
# 1. Use custom exceptions from nodes/errors.py
# 2. RecoverableError for transient issues (rate limits, timeouts)
# 3. FatalError for config/setup issues
# 4. Always log before suppressing
# 5. Include context dict with all exceptions
```
**Effort:** Medium | **Impact:** Medium

---

## 📊 Priority 4: Observability & Debugging

### OBS-001: Add Request ID Correlation
**Files:** `backend/main.py`, `progenitor/main.py`
**Issue:** No request ID propagation between backend and progenitor subprocess.
**Impact:** Cannot trace requests across service boundary.
**Fix:** Generate and propagate X-Request-ID header.
```python
@app.middleware("http")
async def add_request_id(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response
```
**Effort:** Low | **Impact:** Medium

---

### OBS-002: Unified Observability Initialization
**Files:** `observability/__init__.py`, multiple modules
**Issue:** Telemetry initialized lazily and independently in each module.
**Fix:** Single initialization at startup.
```python
class ObservabilityManager:
    @classmethod
    def initialize(cls, config: ProgenitorConfig) -> None:
        setup_langfuse(config.observability)
        setup_tracer(config.telemetry)
        setup_metrics(config.telemetry)
```
**Effort:** Medium | **Impact:** Medium

---

### OBS-003: Add Structured Logging
**Issue:** Many logs use f-strings instead of structured fields.
**Fix:** Use structured logging with trace correlation.
```python
# Before
logger.warning(f"API call failed: {e}")

# After
logger.warning("API call failed", extra={"error": str(e), "trace_id": get_trace_id()})
```
**Effort:** Medium | **Impact:** Low

---

### OBS-004: Add State Snapshot Debugging
**Issue:** No way to replay specific states through the graph for debugging.
**Fix:** Add state snapshot export for debugging.
```python
async def dump_state_for_debug(self, state: ProgenitorState, event: str) -> None:
    """Save state snapshot for debugging graph issues"""
    snapshot = {
        "event": event,
        "timestamp": utcnow_isoformat(),
        "state": sanitize_for_debug(state),  # Redact secrets, truncate large fields
    }
    await self._write_jsonl("debug_snapshots.jsonl", snapshot)
```
**Effort:** Low | **Impact:** Low

---

## 🧪 Priority 5: Testing Improvements

### TEST-001: Add Integration Test for Full Dual-Loop
**File:** `tests/progenitor/test_dual_loop_integration.py`
**Issue:** Integration tests mock LLM, don't test real iteration behavior.
**Fix:** Add deterministic integration test with controlled LLM responses.
**Effort:** Medium | **Impact:** High

---

### TEST-002: Add Property-Based Tests for State Transitions
**Issue:** State transitions are tested with specific cases only.
**Fix:** Use Hypothesis for property-based testing.
```python
from hypothesis import given, strategies as st

@given(st.integers(min_value=0, max_value=10))
def test_iteration_never_exceeds_max(max_iterations):
    # Test that iteration count never exceeds configured maximum
```
**Effort:** Medium | **Impact:** Medium

---

### TEST-003: Add Load Tests for Backend
**Issue:** No performance/load testing.
**Fix:** Add locust or k6 load tests.
**Effort:** Medium | **Impact:** Medium

---

### TEST-004: Improve Test Coverage for Error Paths
**Issue:** Happy path tested, error recovery paths undertested.
**Fix:** Add tests for:
- LLM timeout recovery
- Invalid JSON from LLM
- File lock contention
- Queue overflow handling

**Effort:** Medium | **Impact:** Medium

---

## 🐳 Priority 6: Deployment & Operations

### OPS-001: Add Health Check Endpoint with Detailed Diagnostics
**File:** `app/backend/main.py`
**Current:** Basic health endpoint exists.
**Improve:** Add detailed diagnostics.
```python
@app.get("/health/detailed")
async def detailed_health():
    return {
        "status": "healthy",
        "progenitor": {
            "running": client.is_running(),
            "last_heartbeat": client.last_heartbeat(),
            "queue_depth": client.queue_depth(),
        },
        "llm": {
            "provider": config.llm.provider,
            "last_call_latency_ms": metrics.get("llm_latency"),
        },
        "storage": {
            "state_dir_writable": check_writable(config.storage.state_dir),
            "disk_free_gb": get_disk_free(),
        }
    }
```
**Effort:** Low | **Impact:** Medium

---

### OPS-002: Add Graceful Shutdown Handling
**Issue:** SIGTERM handling exists but could be more robust.
**Fix:** Ensure all in-flight requests complete, flush all buffers.
**Effort:** Low | **Impact:** Medium

---

### OPS-003: Add Docker Health Check ✅ DONE
**File:** `Dockerfile`
**Current:** No HEALTHCHECK instruction.
**Fix:**
```dockerfile
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"
```
**Effort:** Low | **Impact:** Low

---

### OPS-004: Add Resource Limits to docker-compose.yml ✅ DONE
**File:** `docker-compose.yml`
**Issue:** No memory/CPU limits on containers.
**Fix:**
```yaml
services:
  progenitor:
    deploy:
      resources:
        limits:
          memory: 2G
          cpus: '2'
        reservations:
          memory: 512M
```
**Effort:** Low | **Impact:** Low

---

## 📈 Priority 7: Feature Enhancements

### FEAT-001: Add Semantic Capability Search
**File:** `app/progenitor/tools/registry.py`
**Current:** Exact string matching for capabilities.
**Improve:** Add semantic similarity search.
```python
from sentence_transformers import SentenceTransformer

class SemanticRegistry(ToolRegistry):
    def find_by_capability_semantic(self, query: str, threshold: float = 0.7):
        query_embedding = self._encoder.encode(query)
        # Return tools with cosine similarity > threshold
```
**Effort:** Medium | **Impact:** Medium

---

### FEAT-002: Add Backlog Prioritization
**Current:** Backlog processed FIFO.
**Improve:** Add priority field and sorting.
```python
class WorkItem(BaseModel):
    priority: int = 0  # Higher = more urgent

    @property
    def effective_priority(self) -> float:
        """Factor in age and explicit priority"""
        age_hours = (utcnow() - self.created_at).total_seconds() / 3600
        return self.priority + (age_hours * 0.1)  # Age boost
```
**Effort:** Low | **Impact:** Low

---

### FEAT-003: Add Conversation Memory Compression
**Issue:** Full conversation history grows unbounded.
**Fix:** Add summarization for old messages.
```python
async def compress_history(self, messages: list, keep_recent: int = 10) -> list:
    if len(messages) <= keep_recent:
        return messages

    old_messages = messages[:-keep_recent]
    summary = await self.llm_client.summarize(old_messages)

    return [{"role": "system", "content": f"Earlier context: {summary}"}] + messages[-keep_recent:]
```
**Effort:** Medium | **Impact:** Medium

---

### FEAT-004: Add Circuit Breaker for LLM Calls
**Issue:** LLM failures can cascade.
**Fix:** Add circuit breaker pattern.
```python
class CircuitBreaker:
    def __init__(self, failure_threshold: int = 5, reset_timeout: float = 60.0):
        self.failures = 0
        self.state = "closed"
        self.last_failure_time = 0

    async def call(self, fn, *args, **kwargs):
        if self.state == "open":
            if time.time() - self.last_failure_time > self.reset_timeout:
                self.state = "half-open"
            else:
                raise CircuitOpenError("Circuit breaker is open")
        # ...
```
**Effort:** Medium | **Impact:** Medium

---

## Summary Statistics

| Priority | Count | Est. Effort (days) |
|----------|-------|-------------------|
| 🔴 Critical | 6 | 3-5 |
| 🟡 Architecture | 6 | 10-15 |
| 🟢 Code Quality | 7 | 5-7 |
| 📊 Observability | 4 | 3-4 |
| 🧪 Testing | 4 | 4-6 |
| 🐳 Operations | 4 | 2-3 |
| 📈 Features | 4 | 5-7 |
| **Total** | **35** | **32-47** |

---

## Recommended Execution Order

### Sprint 1 (Week 1-2): Critical Security & Reliability
1. SEC-001: Command injection fix
2. SEC-002: Secret redaction fix
3. REL-001: LLM response validation
4. REL-002: Graceful process termination
5. REL-003: Bounded queue
6. CQ-001: Exception handling cleanup

### Sprint 2 (Week 3-4): Architecture Foundation
1. ARCH-002: Dependency injection container
2. ARCH-003: Telemetry decorator
3. CQ-002: Config validation
4. OBS-001: Request ID correlation

### Sprint 3 (Week 5-6): State Management Refactor
1. ARCH-001: Split ProgenitorState
2. ARCH-006: Split StateManager
3. TEST-001: Integration tests

### Sprint 4 (Week 7-8): IPC & Operations
1. ARCH-004: Replace subprocess IPC
2. OPS-001: Health diagnostics
3. OPS-003: Docker health check
4. FEAT-004: Circuit breaker

---

*This backlog should be reviewed and prioritized based on current project needs and team capacity.*
