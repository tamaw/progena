# Execute.py Improvement Recommendations

## High Priority Improvements

### 1. **Extract Agent Build Detection Logic**
**Current Issue**: Agent build detection is repeated 3 times (lines 111-124, 205-217, with similar checks)
```python
# Repeated pattern:
build_settings = state.get("build_settings")
if build_settings:
    build_type = build_settings.get("build_type") if isinstance(build_settings, dict) else getattr(build_settings, "build_type", None)
    if build_type == "agent":
        is_agent_build = True
```

**Recommendation**: Extract to helper function
```python
def _is_agent_build(state: ProgenitorState, experiment_plan_data: dict | None = None) -> tuple[bool, str | None]:
    """Detect if this is an agent build and return agent name if available.

    Returns:
        (is_agent_build, agent_name) tuple
    """
    # Check build_settings
    build_settings = state.get("build_settings")
    if build_settings:
        build_type = build_settings.get("build_type") if isinstance(build_settings, dict) else getattr(build_settings, "build_type", None)
        if build_type == "agent":
            agent_name = build_settings.get("project_name") if isinstance(build_settings, dict) else getattr(build_settings, "project_name", None)
            return True, agent_name

    # Check experiment_plan
    if experiment_plan_data:
        exp_agent_name = experiment_plan_data.get("agent_name")
        if exp_agent_name and exp_agent_name != "unknown_agent":
            return True, exp_agent_name

    # Check current work item type
    current_item = state.get("current_work_item")
    if current_item:
        item_type = _get_item_type(current_item)
        if item_type in ("build_agent", "a2a_call"):
            return True, None

    return False, None
```

### 2. **Break Down `execute_node` Function**
**Current Issue**: The main function is 360+ lines, violating SRP and making testing difficult

**Recommendation**: Extract major paths into separate functions:
```python
async def execute_node(state: ProgenitorState) -> ProgenitorState:
    """Main EXECUTE node - dispatch to appropriate execution strategy."""
    logger.info("EXECUTE: Starting execute phase")
    start_time = time.perf_counter()

    # Setup
    execution_context = _build_execution_context(state)

    # Route to appropriate execution path
    if not execution_context.current_item:
        return await _handle_build_without_work_item(execution_context, harness)

    if execution_context.work_completed:
        return await _handle_already_completed(execution_context)

    if execution_context.is_agent_build and harness:
        return await _handle_agent_exercise(execution_context, harness)

    if execution_context.pending_tools:
        return await _handle_tool_execution(execution_context, tool_registry)

    # Default: no execution needed
    return await _handle_no_execution(execution_context)

class ExecutionContext:
    """Encapsulates execution context to reduce parameter passing."""
    def __init__(self, state: ProgenitorState):
        self.state = state
        self.current_item = state.get("current_work_item")
        self.iteration = state.get("iteration", {}).get("current", 0)
        self.phases = state.get("phases", {})
        self.build_output = self.phases.get("execute", {}).get("result")
        self.experiment_plan_data = self.phases.get("plan", {}).get("experiment_plan")
        self.pending_tools = state.get("pending_tool_calls", [])
        self.work_completed = state.get("work_item_completed", False)
        self.is_agent_build, self.agent_name = _is_agent_build(state, self.experiment_plan_data)
        self.build_settings = state.get("build_settings")
```

### 3. **Improve Error Handling and Recovery**
**Current Issue**: Generic error handling doesn't provide recovery paths

**Recommendation**: Add structured error types and recovery strategies
```python
from enum import Enum

class ExecutionErrorType(str, Enum):
    TOOL_NOT_FOUND = "tool_not_found"
    TOOL_EXECUTION_FAILED = "tool_execution_failed"
    AGENT_NOT_AVAILABLE = "agent_not_available"
    TIMEOUT = "timeout"
    PORT_UNAVAILABLE = "port_unavailable"

class ExecutionError(Exception):
    """Base exception for execution errors with recovery hints."""
    def __init__(self, message: str, error_type: ExecutionErrorType, recoverable: bool = True, recovery_hint: str = ""):
        self.error_type = error_type
        self.recoverable = recoverable
        self.recovery_hint = recovery_hint
        super().__init__(message)

async def _execute_tool_with_retry(
    tool_name: str,
    tool_args: dict,
    tool_registry,
    max_retries: int = 2
) -> dict:
    """Execute tool with exponential backoff retry."""
    last_error = None

    for attempt in range(max_retries + 1):
        try:
            result = await _execute_tool(tool_name, tool_args, tool_registry)
            return {"tool": tool_name, "result": result, "success": True}
        except ExecutionError as e:
            last_error = e
            if not e.recoverable or attempt == max_retries:
                break
            await asyncio.sleep(2 ** attempt)  # Exponential backoff
        except Exception as e:
            last_error = e
            if attempt == max_retries:
                break

    return {
        "tool": tool_name,
        "result": None,
        "success": False,
        "error": str(last_error),
        "error_type": getattr(last_error, 'error_type', 'unknown'),
        "recoverable": getattr(last_error, 'recoverable', False),
    }
```

### 4. **Add Type Hints Throughout**
**Current Issue**: Missing type hints on several functions (e.g., `_exercise_agent`, `_execute_tool`)

**Recommendation**: Complete type annotations
```python
from typing import Any, Callable, Coroutine, Optional

async def _exercise_agent(
    harness: AgentExerciseHarness,
    experiment_plan_data: dict[str, Any],
    current_item: WorkItem | dict[str, Any] | None,
    build_output: dict[str, Any] | None,
    iteration: int,
    state: ProgenitorState,
) -> ExecutionResult:
    """Exercise an agent using the experiment harness."""
    # ...

async def _execute_tool(
    tool_name: str,
    tool_args: dict[str, Any],
    tool_registry: Any,  # Could create ToolRegistry protocol
) -> Any:
    """Execute a single tool and return result."""
    # ...
```

### 5. **Extract Port Management Logic**
**Current Issue**: Port selection logic embedded in `_exercise_agent`

**Recommendation**: Create dedicated port management utilities
```python
# In runtime/port_utils.py (extend existing module)

class PortManager:
    """Manages port allocation for test harness agents."""

    def __init__(self):
        self._allocated_ports: set[int] = set()

    async def allocate_port(
        self,
        preferred_port: int | None = None,
        start_port: int = TEST_HARNESS_PORT_START,
        end_port: int = TEST_HARNESS_PORT_END
    ) -> int:
        """Allocate an available port, tracking allocations."""
        if preferred_port and preferred_port not in self._allocated_ports:
            if await self._is_port_available(preferred_port):
                self._allocated_ports.add(preferred_port)
                return preferred_port

        port = await find_available_port_async(start_port, end_port)
        self._allocated_ports.add(port)
        return port

    async def release_port(self, port: int) -> None:
        """Release an allocated port."""
        self._allocated_ports.discard(port)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self._allocated_ports.clear()
```

### 6. **Centralize ExecutionResult Creation**
**Current Issue**: ExecutionResult created in 5+ places with duplicate logic

**Recommendation**: Factory methods for common patterns
```python
class ExecutionResultFactory:
    """Factory for creating ExecutionResult objects consistently."""

    @staticmethod
    def create_skipped(iteration: int, reason: str = "No work item") -> ExecutionResult:
        """Create a skipped execution result."""
        return ExecutionResult(
            iteration=iteration,
            status=ExecutionStatus.SKIPPED,
            all_errors=[reason],
        ).finalize()

    @staticmethod
    def create_failed(
        iteration: int,
        errors: list[str],
        task_id: str | None = None,
        build_output: dict | None = None
    ) -> ExecutionResult:
        """Create a failed execution result."""
        result = ExecutionResult(
            iteration=iteration,
            task_id=task_id,
            status=ExecutionStatus.FAILED,
            started_at=utcnow(),
            build_output=build_output,
            all_errors=errors,
        )
        result.completed_at = utcnow()
        return result

    @staticmethod
    async def create_from_tool_execution(
        iteration: int,
        tool_results: list[dict],
        errors: list[str],
        build_output: dict | None = None
    ) -> ExecutionResult:
        """Create result from tool execution."""
        execution_output = {
            "build_output": build_output,
            "tool_results": tool_results,
            "all_succeeded": len(errors) == 0,
            "errors": errors,
        }
        return ExecutionResult(
            iteration=iteration,
            build_output=execution_output,
            status=ExecutionStatus.SUCCESS if len(errors) == 0 else ExecutionStatus.PARTIAL,
            all_errors=errors,
        ).finalize()
```

### 7. **Add Structured Logging Context**
**Current Issue**: Logging lacks context and structure

**Recommendation**: Use structured logging with context
```python
import structlog

logger = structlog.get_logger(__name__)

async def execute_node(state: ProgenitorState) -> ProgenitorState:
    log = logger.bind(
        node="execute",
        iteration=state.get("iteration", {}).get("current", 0),
        has_work_item=state.get("current_work_item") is not None
    )

    log.info("execute_phase_started")
    # ... rest of code
    log.info("execute_phase_completed",
             tools_executed=len(tool_results),
             success=len(errors) == 0)
```

### 8. **Improve Agent Entrypoint Discovery**
**Current Issue**: Path discovery logic is embedded and not extensible

**Recommendation**: Strategy pattern for discovery
```python
from abc import ABC, abstractmethod
from pathlib import Path

class EntrypointDiscoveryStrategy(ABC):
    @abstractmethod
    async def discover(self, agent_name: str, project_root: Path) -> str | None:
        """Discover agent entrypoint path."""
        pass

class StandardAgentLocationStrategy(EntrypointDiscoveryStrategy):
    """Check standard app/agents locations."""
    async def discover(self, agent_name: str, project_root: Path) -> str | None:
        potential_paths = [
            project_root / f"app/agents/{agent_name}/agent.py",
            project_root / f"app/agents/{agent_name}/main.py",
            project_root / f"app/agents/{agent_name}/__main__.py",
        ]
        for path in potential_paths:
            if path.exists():
                return str(path)
        return None

class MCPLocationStrategy(EntrypointDiscoveryStrategy):
    """Check MCP server locations."""
    async def discover(self, agent_name: str, project_root: Path) -> str | None:
        potential_paths = [
            project_root / f"app/mcp/{agent_name}/server.py",
            project_root / f"app/mcp/{agent_name}/__main__.py",
        ]
        for path in potential_paths:
            if path.exists():
                return str(path)
        return None

class EntrypointDiscoverer:
    """Discovers agent entrypoints using multiple strategies."""

    def __init__(self, strategies: list[EntrypointDiscoveryStrategy] | None = None):
        self.strategies = strategies or [
            StandardAgentLocationStrategy(),
            MCPLocationStrategy(),
        ]

    async def discover(self, agent_name: str, project_root: Path) -> str | None:
        """Try each strategy until one succeeds."""
        for strategy in self.strategies:
            entrypoint = await strategy.discover(agent_name, project_root)
            if entrypoint:
                return entrypoint
        return None
```

---

## Medium Priority Improvements

### 9. **Add Configuration Class**
Currently constants are scattered. Create config:
```python
@dataclass
class ExecuteConfig:
    max_tool_retries: int = 2
    tool_timeout_seconds: float = 30.0
    port_range_start: int = TEST_HARNESS_PORT_START
    port_range_end: int = TEST_HARNESS_PORT_END
    max_parallel_tools: int = 5
    truncate_result_length: int = 500
    max_error_report: int = 3
```

### 10. **Add Telemetry Decorators**
Reduce boilerplate:
```python
def with_telemetry(node_name: str):
    """Decorator to add telemetry to node functions."""
    def decorator(func):
        @wraps(func)
        async def wrapper(state: ProgenitorState):
            tracer, metrics = _get_telemetry()
            start_time = time.perf_counter()
            span = None

            if tracer:
                span = tracer.start_span(f"node_{node_name}", attributes={...})

            try:
                result = await func(state)
                success = True
                return result
            except Exception as e:
                success = False
                raise
            finally:
                duration = time.perf_counter() - start_time
                if metrics:
                    metrics.record_histogram("node_duration_seconds", duration, {"node": node_name})
                    metrics.increment("node_invocations_total", {"node": node_name, "success": str(success).lower()})
                if span:
                    span.end()
        return wrapper
    return decorator

@with_telemetry("execute")
async def execute_node(state: ProgenitorState) -> ProgenitorState:
    # Simplified - no telemetry boilerplate
    ...
```

### 11. **Parallel Tool Execution**
Currently tools execute sequentially. Add parallel option:
```python
async def _execute_tools_parallel(
    pending_tools: list[dict],
    tool_registry,
    max_concurrent: int = 5
) -> list[dict]:
    """Execute tools in parallel with concurrency limit."""
    semaphore = asyncio.Semaphore(max_concurrent)

    async def execute_with_semaphore(tool_call):
        async with semaphore:
            return await _execute_tool_with_retry(
                tool_name=tool_call.get("tool", "unknown"),
                tool_args=tool_call.get("arguments", {}),
                tool_registry=tool_registry
            )

    tasks = [execute_with_semaphore(tool) for tool in pending_tools]
    return await asyncio.gather(*tasks, return_exceptions=True)
```

### 12. **Add Cancellation Support**
Handle graceful shutdown:
```python
async def execute_node(state: ProgenitorState) -> ProgenitorState:
    try:
        # Main execution logic
        ...
    except asyncio.CancelledError:
        logger.info("Execute node cancelled gracefully")
        # Create suspended execution result
        exec_result = ExecutionResultFactory.create_suspended(
            iteration=iteration,
            reason="Cancelled by user or system"
        )
        return {"phases": {"execute": {"completed": False, "result": exec_result.model_dump()}}}
```

### 13. **Extract State Access to Helper**
Reduce repeated dict.get() calls:
```python
class StateAccessor:
    """Type-safe state access helper."""

    def __init__(self, state: ProgenitorState):
        self._state = state

    @property
    def current_work_item(self) -> WorkItem | None:
        return self._state.get("current_work_item")

    @property
    def iteration(self) -> int:
        return self._state.get("iteration", {}).get("current", 0)

    @property
    def build_output(self) -> dict | None:
        return self._state.get("phases", {}).get("execute", {}).get("result")

    # ... etc
```

---

## Low Priority / Nice-to-Have

### 14. **Add Execution Middleware**
For cross-cutting concerns:
```python
class ExecutionMiddleware(ABC):
    @abstractmethod
    async def before_execution(self, context: ExecutionContext) -> ExecutionContext:
        pass

    @abstractmethod
    async def after_execution(self, context: ExecutionContext, result: ProgenitorState) -> ProgenitorState:
        pass

class ValidationMiddleware(ExecutionMiddleware):
    """Validate execution pre/post conditions."""
    async def before_execution(self, context: ExecutionContext) -> ExecutionContext:
        # Validate state
        return context

    async def after_execution(self, context: ExecutionContext, result: ProgenitorState) -> ProgenitorState:
        # Validate result
        return result
```

### 15. **Add Execution Metrics Collection**
Detailed performance tracking:
```python
@dataclass
class ExecutionMetrics:
    total_duration_ms: float
    tool_count: int
    tool_success_rate: float
    avg_tool_duration_ms: float
    agent_exercise_duration_ms: float | None
    port_allocation_duration_ms: float | None

    def to_dict(self) -> dict:
        return asdict(self)
```

### 16. **Improve Test Hooks**
Add dependency injection for testing:
```python
class ExecuteNodeDependencies:
    """Injectable dependencies for execute node."""
    def __init__(
        self,
        tool_registry,
        harness: AgentExerciseHarness | None = None,
        port_manager: PortManager | None = None,
        config: ExecuteConfig | None = None,
    ):
        self.tool_registry = tool_registry
        self.harness = harness
        self.port_manager = port_manager or PortManager()
        self.config = config or ExecuteConfig()

def create_node(deps: ExecuteNodeDependencies):
    """Create execute node with injected dependencies."""
    async def execute_node(state: ProgenitorState) -> ProgenitorState:
        # Use deps.* everywhere
        pass
    return execute_node
```

---

## Summary of Benefits

1. **Maintainability**: Smaller, focused functions easier to understand and modify
2. **Testability**: Dependency injection and separation enables unit testing
3. **Extensibility**: Strategy patterns allow adding new discovery/execution methods
4. **Reliability**: Better error handling and recovery mechanisms
5. **Performance**: Parallel tool execution and resource pooling
6. **Observability**: Structured logging and detailed metrics
7. **Type Safety**: Complete type hints catch errors at dev time
8. **DRY**: Eliminate duplication across the codebase

## Implementation Priority

**Phase 1** (Critical):
- #1: Extract agent build detection
- #2: Break down execute_node
- #3: Improve error handling
- #4: Add type hints

**Phase 2** (Important):
- #5: Port management
- #6: ExecutionResult factory
- #8: Entrypoint discovery

**Phase 3** (Enhancement):
- #10: Telemetry decorators
- #11: Parallel execution
- #13: State accessor

**Phase 4** (Polish):
- #7: Structured logging
- #9: Configuration class
- #15: Metrics collection
