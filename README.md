# Progenitor Agent

A perpetual, conversational personal assistant and bootstrap builder for the TARDIS system.

## Overview

Progenitor is an always-on agent that:
- Accepts user messages conversationally
- Routes requests: answer directly, delegate via A2A, or build/upgrade agents
- Uses Copilot CLI as the builder actuator
- Maintains persistent state and logs
- Runs as a perpetual loop in Docker

## Quick Start

### Prerequisites

- Python 3.12+
- Docker (optional, recommended)
- OpenAI API key
- Copilot CLI (for build operations)

### Environment Setup

```bash
# Set your OpenAI API key
export OPENAI_API_KEY=your_key_here

# Install dependencies
pip install -r requirements.txt
```

### Running Locally

```bash
python -m app.progenitor.main
```

### Running with Docker

```bash
# Build and run
docker-compose up -d

# View logs
docker-compose logs -f progenitor

# Stop
docker-compose down
```

## Architecture

### LangGraph Topology

```
Intake → TimeCheck → EnqueueDue → Decide → Planner → PickNext →
WorktreePrep → Act → Validate → Register → CommitCore → Respond →
CycleLimit → (PickNext | Idle)
```

### Directory Structure

```
app/
  progenitor/
    main.py          # Service entrypoint
    graph.py         # LangGraph topology
    llm/             # OpenAI wrapper + prompts
    tools/           # Copilot, git, MCP clients
    runtime/         # Loop, scheduler, time
    state/           # State management + models
    nodes/           # LangGraph node implementations
  agents/            # A2A agents (created by Progenitor)
  mcp/               # MCP servers (created by Progenitor)
config/
  copilot.json         # Copilot CLI configuration
  progenitor.json    # Runtime configuration
state/               # Persisted state (JSON)
logs/                # Append-only logs (JSONL)
worktrees/           # Git worktrees for builds
```

### State Files

- `state/current_state.json` - Runtime state
- `state/backlog.json` - Work item queue
- `state/registry.json` - Agent and tool registry
- `state/schedule.json` - Scheduled tasks

### Log Files

- `logs/conversation.jsonl` - All user/assistant messages
- `logs/decisions.jsonl` - Routing decisions
- `logs/copilot_calls.jsonl` - Copilot CLI invocations
- `logs/validation.jsonl` - Validation results
- `logs/schedule.jsonl` - Schedule events

## Configuration

### progenitor.json

```json
{
  "timezone": "UTC",
  "idle_backoff_seconds": 5,
  "max_backoff_seconds": 60,
  "max_actions_per_cycle": 10,
  "checkpoint_interval_seconds": 30
}
```

### copilot.json

```json
{
  "command": "copilot",
  "default_flags": ["--yolo"],
  "timeout": 300,
  "model": "claude-haiku-4.5"
}
```

## Usage

Progenitor accepts natural language input and decides how to handle it:

1. **Direct answers**: Simple questions answered immediately
2. **A2A delegation**: Calls existing agents for specialized tasks
3. **MCP tools**: Uses registered MCP servers for tool access
4. **Build/Upgrade**: Creates new agents or tools via Copilot CLI
5. **Scheduling**: Creates time-based recurring or one-off tasks

### Examples

```
User: What time is it?
→ Direct answer

User: Send an email to John about the meeting
→ A2A delegation (if email agent exists) or build agent

User: Remind me every day at 9am to check my tasks
→ Schedule creation

User: I need a tool that can fetch weather data
→ Build MCP server
```

## Architecture

Progenitor implements a dual-loop architecture based on the [loop.spec.md](docs/loop.spec.md):

- **Outer Loop**: Ideal state management (define, refine, verify)
- **Inner Loop**: OBSERVE → THINK → PLAN → BUILD → EXECUTE → VERIFY → LEARN

See [docs/](docs/) for detailed documentation:
- [SPEC.md](docs/SPEC.md) - Original system specification
- [loop.spec.md](docs/loop.spec.md) - Inner loop architecture
- [ARCHITECTURE_DIAGRAM.md](docs/ARCHITECTURE_DIAGRAM.md) - Visual architecture
- [CHAT_SERVER.md](docs/CHAT_SERVER.md) - Chat server API
- [DOCKER.md](docs/DOCKER.md) - Docker deployment

## Development

### Running Tests

```bash
# All tests
pytest tests/ -v

# Specific test suite
pytest tests/test_dual_loop.py -v
```

### Adding New Capabilities

1. Create an agent in `app/agents/`
2. Or create an MCP server in `app/mcp/`
3. Register in `state/registry.json`

Or simply ask Progenitor to build it for you!

## License

MIT
