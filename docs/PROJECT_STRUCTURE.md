# Progenitor Project Structure

## Root Directory
```
progenitor/
├── app/                    # Application source code
├── tests/                  # Test suite
├── docs/                   # Documentation
├── config/                 # Configuration files
├── data/                   # Runtime data
│   ├── state/              # State files (gitignored except .gitkeep)
│   └── prompts/            # Editable prompt templates
├── examples/               # Usage examples
├── scripts/                # CLI scripts and utilities
├── logs/                   # Application logs (gitignored)
├── worktrees/              # Git worktrees for builds (gitignored)
├── .venv/                  # Python virtual environment
│
├── README.md               # Main project documentation
├── requirements.txt        # Python dependencies
├── pytest.ini              # Test configuration
├── docker-compose.yml      # Docker orchestration
└── Dockerfile              # Container definition
```

## Scripts Directory
```
scripts/
├── server_cli.py           # Server control (test/serve/mock)
├── start_all.ps1           # Start all services
├── chat_cli.py             # Interactive chat client
├── chat_cli_httpx.py       # Async chat client (httpx)
├── validate_app.py         # App validation script
└── validate_docker.py      # Docker validation script
```

## Data Directory
```
data/
├── state/                  # Runtime state (gitignored)
│   ├── backlog.json        # Work item queue
│   ├── current_state.json  # Agent state
│   ├── registry.json       # Registered agents/tools
│   └── schedule.json       # Scheduled tasks
│
└── prompts/                # Editable prompts
    ├── genesis.md          # System prompt
    ├── decide.md           # Decision routing
    ├── planner.md          # Work planning
    ├── build.md            # Build specifications
    ├── copilot_task.md     # Copilot CLI tasks
    └── agent_structure.md  # Agent directory structure
```

## Application Structure
```
app/
├── progenitor/             # Main agent package
│   ├── __init__.py
│   ├── main.py             # Service entry point
│   ├── config.py           # Configuration management
│   ├── graph.py            # Original graph implementation
│   ├── graph_dual_loop.py  # Loop.spec.md implementation ⭐
│   │
│   ├── nodes/              # 19 LangGraph nodes
│   │   ├── intake.py       # User input processing
│   │   ├── decide.py       # Decision making
│   │   ├── observe.py      # OBSERVE phase ⭐
│   │   ├── think.py        # THINK phase ⭐
│   │   ├── planner.py      # PLAN phase ⭐
│   │   ├── act.py          # BUILD phase ⭐
│   │   ├── execute.py      # EXECUTE phase ⭐ NEW
│   │   ├── verify_loop.py  # VERIFY phase ⭐
│   │   ├── learn.py        # LEARN phase ⭐
│   │   └── ... (11 more)
│   │
│   ├── state/              # State management
│   │   ├── models.py       # Pydantic models
│   │   ├── manager.py      # State persistence
│   │   ├── ideal_state.py  # Ideal state (loop.spec.md) ⭐
│   │   ├── verifier.py     # Verification system ⭐
│   │   └── helpers.py      # State utilities
│   │
│   ├── llm/                # LLM integration
│   │   ├── client.py       # LLM client wrapper
│   │   └── prompts.py      # Prompt templates
│   │
│   ├── tools/              # Tool integrations
│   │   ├── registry.py     # Tool/agent registry
│   │   ├── copilot.py      # Copilot CLI integration
│   │   ├── git.py          # Git operations
│   │   └── mcp.py          # MCP/A2A clients
│   │
│   └── runtime/            # Runtime utilities
│       ├── loop.py         # Main loop runner
│       ├── scheduler.py    # Task scheduling
│       └── time_utils.py   # Time utilities
│
├── chat_server/            # HTTP chat server
│   ├── main.py             # FastAPI server
│   ├── client.py           # Client SDK
│   ├── config.py           # Server config
│   └── models.py           # API models
│
├── agents/                 # Built agents (empty initially)
└── mcp/                    # MCP servers (empty initially)
```

## Tests Structure
```
tests/
├── test_dual_loop.py       # Loop.spec.md tests (20 tests) ⭐
├── test_dual_loop_integration.py  # Integration tests
├── test_models.py          # Model tests
├── test_nodes.py           # Node tests
├── test_state_manager.py   # State tests
├── test_registry.py        # Registry tests
├── test_scheduler.py       # Scheduler tests
├── test_helpers.py         # Helper tests
├── test_config.py          # Config tests
│
└── chat_server/            # Chat server tests
    ├── test_chat_server.py
    ├── test_integration.py
    ├── test_e2e.py
    └── mock_progenitor.py
```

## Documentation
```
docs/
├── README.md                        # Documentation index
├── SPEC.md                          # Original specification
├── loop.spec.md                     # Inner loop spec ⭐
├── ARCHITECTURE_DIAGRAM.md          # System architecture
├── LOOP_IMPROVEMENTS.md             # Improvement notes
├── LOOP_IMPROVEMENTS_SUMMARY.md     # Improvement summary
├── VALIDATION_SUMMARY.md            # Validation results
├── CHAT_SERVER.md                   # Chat server API
└── DOCKER.md                        # Docker guide
```

## Configuration
```
config/
├── progenitor.json         # Runtime configuration
└── copilot.json            # Copilot CLI settings
```

## Key Features

### ⭐ Loop.spec.md Implementation
The dual-loop architecture is the recommended implementation:

**Outer Loop**: Ideal state management
- Define success criteria
- Refine based on results
- Verify against quality bar

**Inner Loop**: 7-phase iteration
1. OBSERVE - Refine ideal state
2. THINK - Generate approaches
3. PLAN - Choose experiment
4. BUILD - Create candidate
5. EXECUTE - Run tools (NEW)
6. VERIFY - Score output
7. LEARN - Update strategy

### Graph Implementations
- `graph.py` - Original single-loop
- `graph_dual_loop.py` - Loop.spec.md implementation (default)

### Test Coverage
- 140 total tests passing
- 20 tests specifically for loop.spec.md
- Full coverage of all nodes and phases

## Recent Improvements
- ✓ Added EXECUTE phase (separating BUILD from tool execution)
- ✓ Enhanced anti-criteria extraction from violations
- ✓ Improved best artefact tracking on early stop
- ✓ Organized documentation into docs/ folder
- ✓ Removed build artifacts (__pycache__)
- ✓ Created documentation index
