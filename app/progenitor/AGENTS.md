# Progenitor Core - GitHub Copilot Agent

## LangGraph Agent Loop

The core perpetual agent implementing the "Last Algorithm" dual-loop pattern.

### Agent Capabilities
- LangGraph state machine design and implementation
- Async Python patterns and coroutines
- State management and persistence
- LLM integration (Azure OpenAI, OpenAI)
- Agent decision-making and routing
- Build orchestration via Copilot CLI
- A2A (Agent-to-Agent) communication
- Scheduled task execution

### Architecture

#### Dual-Loop Pattern
**Outer Loop (Perpetual):**
```
Intake → TimeCheck → EnqueueDue → Decide → [InnerLoop] → Respond → Idle → repeat
```

**Inner Loop (Iteration):**
```
Observe → Think → Plan → Build → Execute → Validate → Verify → Learn
```

#### Directory Structure
```
progenitor/
├── config.py            # Configuration dataclasses
├── graph.py             # LangGraph topology and routing
├── main.py              # Entry point
├── nodes/               # LangGraph node implementations
│   ├── decide.py        # Decision routing node
│   ├── intake.py        # Message intake node
│   ├── planner.py       # Planning node
│   ├── builder.py       # Build execution node
│   ├── respond.py       # Response generation node
│   └── ...
├── runtime/
│   └── loop.py          # Perpetual loop execution
├── state/
│   ├── models.py        # Pydantic state models
│   └── manager.py       # State persistence manager
└── llm/
    └── client.py        # LLM client abstraction
```

### Key Components

#### State Models (`state/models.py`)
- `ProgenitorState`: TypedDict for LangGraph nodes
- `WorkItem`: Backlog items with types (BUILD_AGENT, A2A_CALL, SYSTEM_TOOL)
- `Decision`: Routing decisions with actions (ANSWER_DIRECTLY, DELEGATE_A2A, BUILD_AGENT)
- `VerificationResult`: Build validation results

#### Node Pattern
All nodes follow this factory pattern:
```python
def create_node(state_manager, llm_client=None) -> Callable[[ProgenitorState], Awaitable[dict]]:
    async def node(state: ProgenitorState) -> dict:
        # Node logic - return partial state update
        return {"key": "value"}
    return node
```

#### Graph Routing (`graph.py`)
Routing functions determine flow:
- `should_continue()`: `pick_next` | `idle` | `end`
- `route_after_decide()`: `planner` | `pick_next` | `respond`
- `route_after_validate()`: `register` | `pick_next`

### Development

#### Running the Agent
```bash
# Activate venv
& .\.venv\Scripts\Activate.ps1

# Run directly
python -m app.progenitor.main

# With mock LLM (for testing)
$env:PROGENITOR_MOCK_LLM = "true"
python -m app.progenitor.main
```

#### Testing
```bash
# Run all progenitor tests
pytest tests/progenitor/ -v

# Run specific test file
pytest tests/progenitor/test_nodes.py -v

# Run with coverage
pytest tests/progenitor/ --cov=app.progenitor
```

### Configuration

#### Environment Variables
- `PROGENITOR_LLM_PROVIDER`: `azure` | `openai`
- `AZURE_OPENAI_*`: Azure deployment settings
- `OPENAI_API_KEY`: OpenAI API key
- `PROGENITOR_MOCK_LLM`: Enable mock mode
- `PROGENITOR_DATA_DIR`: Data directory path

#### Config Files
- `current_state.json`: User session state
- `build_state.json`: Build backlog and results
- `schedule.json`: Scheduled tasks

### Key Patterns

#### State Persistence
```python
# Load state
state = await state_manager.load_state()

# Update state
await state_manager.update_state({"key": "value"})

# Save explicitly
await state_manager.save_state()
```

#### LLM Calls
```python
# Decision routing
decision = await llm_client.decide(state)

# Planning
plan = await llm_client.plan(work_item, context)

# Verification
result = await llm_client.verify(build_output)
```

#### Secret Redaction
`redact_secrets()` auto-redacts API keys in logs. Patterns cover OpenAI, Azure, GitHub, Anthropic, and AWS keys.

### Testing Conventions
- Use `pytest-asyncio` with `asyncio_mode = auto`
- Mock LLM client: `mock_llm = AsyncMock(); mock_llm.decide.return_value = {...}`
- Mock state manager: `mock_state_manager = AsyncMock()`
- Example: `tests/progenitor/test_nodes.py`

### Important Notes
- Always activate venv before Python commands
- State persists automatically between runs
- Exponential backoff when idle
- Interrupt-safe (graceful shutdown on Ctrl+C)
- Secret redaction in logs

### References
- Loop Spec: `docs/loop.spec.md`
- Architecture: `docs/ARCHITECTURE_DIAGRAM.md`
- State Models: `app/progenitor/state/models.py`
- Graph Definition: `app/progenitor/graph.py`
