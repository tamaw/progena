# Progenitor - GitHub Copilot Agents

## Root Project Agent

This is the main Progenitor agent system - a perpetual, conversational personal assistant implementing the "Last Algorithm" dual-loop pattern.

### Agent Capabilities
- Full-stack development (Python backend, React frontend)
- LangGraph state machine design and implementation
- DeepAgent integration (middleware, skills, memory, subagents)
- Docker containerization and deployment
- API design (FastAPI, OpenAI-compatible endpoints)
- Testing with pytest and async patterns
- State management and persistence
- IPC and async queue patterns

### Project Structure
```
progenitor/
├── app/
│   ├── progenitor/     # Core agent logic (LangGraph)
│   │   ├── agent/      # DeepAgent integration layer
│   │   ├── nodes/      # LangGraph node implementations
│   │   ├── state/      # State models and persistence
│   │   ├── tools/      # System tools (23+)
│   │   └── runtime/    # Loop runner, scheduler, DeepAgent adapter
│   ├── backend/        # FastAPI server
│   ├── frontend/       # React/Vite UI
│   ├── agents/         # Agent implementations
│   └── mcp/            # MCP server integrations
├── data/
│   ├── prompts/
│   └── skills/         # DeepAgent SKILL.md files
├── tests/              # Test suites
└── docs/               # Documentation
```

### Development Workflow
1. **Setup**: Activate venv before any Python commands
   ```powershell
   & .\.venv\Scripts\Activate.ps1  # Windows
   ```
2. **Testing**: `pytest` (auto-discovers all tests)
3. **Type Checking**: `mypy app/`
4. **Linting**: `ruff check app/`
5. **Run Dev**: VS Code Task "Start All (dev)"

### Key Patterns
- **State Models**: Pydantic v2 models in `app/progenitor/state/models.py`
- **Node Pattern**: Factory functions returning async callables
- **Graph Routing**: Conditional edges defined in `app/progenitor/graph.py`
- **DeepAgent Factory**: `app/progenitor/agent/deep_agent.py` — `create_progenitor_agent()`
- **Configuration**: Environment variables > JSON config files
- **Testing**: `pytest-asyncio` with `asyncio_mode = auto`

### Important Notes
- Always activate venv before Python commands
- Mock mode available: `$env:PROGENITOR_MOCK_LLM = "true"`
- Backend runs on `:8000`, Frontend on `:5173`
- State persists in JSON files: `current_state.json`, `build_state.json`, and `data/config/schedule.json`

### Repo Safety Policy (Copilot)
- **Do not merge PRs**. Create PRs and wait for human approval/merge.
- **Do not trigger deployments** unless explicitly instructed by the human (workflow_dispatch is allowed only on request).
- If a workflow run is accidentally started that could deploy, **cancel it immediately** and notify the human.

### References
- Architecture: `docs/ARCHITECTURE_DIAGRAM.md`
- System Specification: `docs/SPEC.md`
- Backend Details: `docs/BACKEND.md`
- Local Startup: `docs/LOCAL_STARTUP.md`
- Project Structure: `docs/PROJECT_STRUCTURE.md`
