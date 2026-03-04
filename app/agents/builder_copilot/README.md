# Builder Copilot

A minimal, autonomous agent for repository changes that uses GitHub Copilot CLI, git worktrees, and testing/linting commands to produce pull requests at the press of a button.

## Overview

Builder Copilot is an A2A (Agent-to-Agent) protocol server that:
- Accepts work requests over HTTP (A2A JSON-RPC or text-based)
- Plans changes before implementation
- Uses GitHub Copilot CLI for code generation (`copilot`, `copilot-pr`)
- Manages isolated git worktrees for each task
- Runs test and lint validation
- Creates pull requests automatically
- Reports results back to the caller

**Port**: 8108 (configurable)  
**Protocol**: A2A (Agent-to-Agent) JSON-RPC + HTTP/REST  
**Model**: Azure OpenAI (gpt-5.3-codex by default)

## Purpose

Builder Copilot is designed as a *worker agent* that:
1. Receives high-level change requests (in plain English)
2. Breaks them down into plan → build → test → lint → PR steps
3. Uses *skill files* to guide each step
4. Produces minimal, validated pull requests

It bridges the gap between natural language requests and validated code changes by automating the repetitive scaffold work (worktree setup, test loops, linting, PR mechanics).

## Capabilities

### Available Skills

Six **step skills** are built into the agent. These are files in `skills/<skill_id>/SKILL.md` that guide execution:

| Skill | Description | Key Tools |
|-------|-------------|-----------|
| **plan** | Normalize request and create a worktree; inspect target code | `summarize_request`, `create_worktree`, `python_search` |
| **build** | Implement changes using GitHub Copilot CLI | `run_copilot_build`, `run_worktree_command` |
| **test** | Run focused validation commands | `run_worktree_command` |
| **lint** | Run lint/format checks | `run_worktree_command` |
| **pr** | Commit, push, and create a pull request | `commit_changes`, `create_pull_request` |
| **python-search** | Search Python code for symbols, imports, tests | `python_search`, `grep` |

### Integrated Tools

Builder Copilot uses tools from the underlying builder-agent infrastructure:

| Category | Tools |
|----------|-------|
| **Copilot CLI** | `copilot`, `copilot-pr` (code generation) |
| **Git** | `git worktree`, `git commit`, `git push` |
| **GitHub CLI** | `gh pr create` (pull request creation) |
| **Code Search** | Ripgrep, Python AST-based search, grep |
| **Execution** | Shell command execution in worktree context |

## Configuration

### Environment Variables

Builder Copilot reads configuration from environment variables. All are optional with sensible defaults.

#### LLM Configuration
```env
# Azure OpenAI endpoint (required)
AZURE_OPENAI_BASE_URL=https://<resource>.openai.azure.com/openai/v1
AZURE_OPENAI_API_KEY=<your-api-key>

# Model selection (defaults shown)
PROGENITOR_MODEL_DEFAULT=gpt-5.2-chat
PROGENITOR_MODEL_PLANNER=gpt-5.2-chat        # Optional: override planner model
PROGENITOR_MODEL_BUILDER=gpt-5.3-codex       # Optional: override builder model
```

#### Copilot CLI Configuration
```env
# GitHub Copilot CLI settings
COPILOT_GITHUB_TOKEN=<github-token>  # Required for copilot & copilot-pr commands
GH_TOKEN=<github-token>               # GitHub CLI authentication
PROGENITOR_COPILOT_BIN=copilot        # Path to copilot binary
PROGENITOR_COPILOT_MODEL=gpt-5.3-codex
PROGENITOR_COPILOT_TIMEOUT_SECONDS=1200
PROGENITOR_COPILOT_DEFAULT_FLAGS=--yolo     # Comma-separated flags for copilot CLI
```

#### Storage Paths
```env
PROGENITOR_PROJECT_ROOT=/path/to/repo           # Repository root (workspace)
PROGENITOR_WORKTREE_DIR=/path/to/worktrees      # Where to store git worktrees
PROGENITOR_STATE_DIR=/data/state                # State persistence
PROGENITOR_LOG_DIR=/logs                        # Log output
PROGENITOR_PROMPTS_DIR=/prompts                 # Skill file location
```

#### Git Identity (for commits)
```env
GIT_AUTHOR_NAME="Builder Copilot"
GIT_AUTHOR_EMAIL="builder@example.com"
```

#### Runtime
```env
PROGENITOR_LOG_LEVEL=info              # debug, info, warn, error
PROGENITOR_LOG_FORMAT=text             # text or json
```

### Docker Environment

When running in Docker (see `Dockerfile`):
- Ports are mounted: `/data`, `/logs`, `/worktrees`
- Entrypoint: `uvicorn app.agents.builder_copilot.agent:app --host 0.0.0.0 --port 8108`
- User runs as non-root (`runner:agent`)
- Required tools pre-installed: git, GitHub CLI, Copilot CLI, ripgrep, jq

## Invoking via A2A Protocol

### Agent Card

The agent exposes a standard A2A Agent Card at `GET /.well-known/agent.json`:

```json
{
  "name": "Builder Copilot",
  "description": "Minimal DeepAgent builder...",
  "version": "0.1.0",
  "url": "http://localhost:8108",
  "capabilities": {
    "streaming": false,
    "state_transition_history": true
  },
  "default_input_modes": ["text/plain"],
  "default_output_modes": ["text/plain"],
  "skills": [
    {
      "id": "plan",
      "name": "Plan",
      "description": "Plan a small repository change before editing.",
      "tags": ["builder", "copilot", "plan"]
    },
    // ... (build, test, lint, pr, python-search)
  ]
}
```

### Health Check

**Endpoint**: `GET /health`

**Response**:
```json
{
  "status": "healthy",
  "name": "Builder Copilot",
  "version": "0.1.0",
  "uptime_seconds": 3600.5
}
```

### Invoke Task

**Endpoint**: `POST /a2a`

**Request** (A2A JSON-RPC):
```json
{
  "task_id": "unique-task-id",
  "context_id": "context-123",
  "message": {
    "role": "user",
    "parts": [
      {
        "root": {
          "text": "Add a new authentication middleware to app/auth.py that validates JWT tokens"
        }
      }
    ],
    "metadata": {
      "worktree": "auth-middleware-123",  // Optional: use existing worktree
      "skip_test": false,
      "skip_lint": false
    }
  }
}
```

**Response** (async, via event stream):
```
TaskStatusUpdateEvent (working)
→ TaskStatusUpdateEvent (completed/failed)
→ TaskArtifactUpdateEvent (final result)
```

### Python Client Example

Using the a2a SDK:

```python
import httpx
from a2a.client import A2AClient

client = A2AClient(url="http://localhost:8108")

# Get agent capabilities
card = await client.get_agent_card()
print(f"Skills: {[s.id for s in card.skills]}")

# Invoke task
task_id = "my-task-123"
result = await client.execute_task(
    task_id=task_id,
    message={
        "role": "user",
        "parts": [{"root": {"text": "Add error handling to database queries"}}],
        "metadata": {"skip_test": False}
    }
)
print(result)
```

## Usage Examples

### Example 1: Simple Code Change

**Request**:
```
Add a logging utility to app/utils/logger.py that uses Python's logging module.
```

**Agent Flow**:
1. **Plan**: Create worktree, inspect existing utils
2. **Build**: Use copilot to generate logger.py
3. **Test**: Run `pytest tests/test_utils.py -v`
4. **Lint**: Run `ruff check app/utils/logger.py && black --check app/utils/logger.py`
5. **PR**: Commit and create PR with title "Add logging utility"

**Result**: PR URL returned (e.g., `https://github.com/owner/repo/pull/123`)

### Example 2: Bug Fix with Existing Tests

**Request** (with metadata):
```
{
  "message": "Fix the date parsing bug in app/core/parser.py",
  "metadata": {
    "skip_lint": false,
    "skip_test": false
  }
}
```

**Agent Flow**:
1. **Plan**: Normalize request, create/reuse worktree
2. **Python-Search**: Find `parse_date()` function, inspect tests
3. **Build**: Use copilot to fix the bug
4. **Test**: Run focused test: `pytest tests/core/test_parser.py::test_parse_date -v`
5. **Lint**: Run full lint suite
6. **PR**: Create PR with auto-generated title from git diff

### Example 3: Feature with Architecture Inspection

**Request**:
```
Add caching layer to the database models using Redis.
Please inspect the current model structure first.
```

**Agent Flow**:
1. **Plan**: Create worktree
2. **Python-Search**: Inspect models, find Redis integration patterns
3. **Build**: Generate cache layer using copilot
4. **Test**: Run `pytest tests/models/ -v`
5. **Lint**: Validate imports and style
6. **PR**: Create PR with detailed description

## Architecture

### Component Overview

```
FastAPI HTTP Server (port 8108)
  ├── A2A Agent Card endpoint (GET /.well-known/agent.json)
  ├── Health endpoint (GET /health)
  └── Task executor (POST /a2a)
       │
       └── BuilderCopilotService (lazy-initialized)
            ├── Azure OpenAI Client (planning, building)
            ├── DeepAgent Graph (LangGraph-based workflow)
            │   ├── Skill router (plan → build → test → lint → pr)
            │   ├── Tool executor (copilot, git, gh, run_worktree_command)
            │   └── State manager (thread-safe, per-task)
            └── Tool Runtime
                ├── CopilotCLI wrapper
                ├── GitWorktreeManager
                └── shell command executor
```

### Request Flow

```
1. Client POSTs /a2a with task
   ↓
2. BuilderCopilotExecutor receives RequestContext
   ↓
3. Extract request text & metadata from Message
   ↓
4. BuilderCopilotService.process(request_text, context)
   ├── LLM preflight plan (optional)
   ├── Build execution prompt
   └── invoke DeepAgent graph
   ↓
5. DeepAgent routes through skills:
   ├─ Plan node (normalize, create worktree, inspect code)
   ├─ Build node (run copilot with Copilot CLI or LLM)
   ├─ Test node (run test command)
   ├─ Lint node (run lint command)
   └─ PR node (commit, push, create PR)
   ↓
6. Extract result text from graph output
   ↓
7. Send TaskStatusUpdateEvent & TaskArtifactUpdateEvent back to client
```

### State Management

Each task has isolated state:
- **Worktree path**: `<PROGENITOR_WORKTREE_DIR>/<task_id>/`
- **Thread ID**: `builder_copilot_<uuid>`
- **LangGraph checkpoint**: Stored in memory (resumable via thread_id)

### Tool Integration

#### Copilot CLI
```python
from app.agents.builder_agent.tools.copilot import CopilotCLI

copilot = CopilotCLI(bin_path="copilot", timeout=1200)

# Generate code
await copilot.run(
    args=["--prompt", "Add error handling"],
    cwd="/path/to/worktree",
    target_path="app/errors.py"
)
```

#### Git Worktree Manager
```python
from app.agents.builder_agent.tools.git import GitWorktreeManager

git = GitWorktreeManager(repo_root="/path/to/repo")

# Create worktree
worktree = await git.create_worktree(
    base_branch="main",
    branch_name="feature/my-feature"
)
# → "/path/to/worktrees/feature/my-feature"
```

#### GitHub CLI
```python
from app.agents.builder_agent.tools.gh import gh_create_pr_from_worktree

# Create PR
pr_url = await gh_create_pr_from_worktree(
    worktree_path="/path/to/worktree",
    title="Add feature",
    body="Closes #123"
)
# → "https://github.com/owner/repo/pull/456"
```

### Skill Files

Each skill is defined in a markdown file at `skills/<skill_id>/SKILL.md`:

**Format**:
```yaml
---
name: plan
description: Plan the requested change before editing.
allowed-tools: summarize_request create_worktree python_search run_worktree_command
---

# Plan

- Normalize the request first.
- Create a worktree unless one is already provided.
- ...
```

DeepAgent reads these at startup and makes them available to the LLM as execution guides.

### System Prompt

The agent uses a predefined system prompt (`SYSTEM_PROMPT` in `deep_agent.py`):

```
You are Builder Copilot, a minimal build agent for repository changes.

Work in this order unless the user explicitly tells you otherwise:
1. plan
2. build
3. test
4. lint
5. pr

Rules:
- Start by normalizing the request, then create a worktree unless one is already provided.
- Use python_search to inspect the codebase before major edits.
- Use run_copilot_build for implementation work and run_worktree_command for validation.
- ...
```

## Running Builder Copilot

### Locally (Development)

**1. Activate virtual environment**:
```bash
# Windows
& .\.venv\Scripts\Activate.ps1

# Linux/Mac
source .venv/bin/activate
```

**2. Install dependencies**:
```bash
pip install -r app/agents/builder_copilot/requirements.txt
pip install -r requirements.lock.txt  # Full project dependencies
```

**3. Set environment variables**:
```bash
# Create a .env file or export manually
export AZURE_OPENAI_BASE_URL="https://..."
export AZURE_OPENAI_API_KEY="..."
export COPILOT_GITHUB_TOKEN="..."
export PROGENITOR_PROJECT_ROOT="/path/to/repo"
```

**4. Start the server**:
```bash
python -m uvicorn app.agents.builder_copilot.agent:app --host 0.0.0.0 --port 8108 --reload
```

Server will be available at `http://localhost:8108`.

### Docker

**Build**:
```bash
docker build -f app/agents/builder_copilot/Dockerfile -t builder-copilot:latest .
```

**Run**:
```bash
docker run -d \
  --name builder-copilot \
  -p 8108:8108 \
  -e AZURE_OPENAI_BASE_URL="https://..." \
  -e AZURE_OPENAI_API_KEY="..." \
  -e COPILOT_GITHUB_TOKEN="..." \
  -e PROGENITOR_REPO_URL="https://github.com/owner/repo.git" \
  -v /path/to/data:/app/data \
  -v /path/to/logs:/app/logs \
  -v /path/to/worktrees:/app/worktrees \
  builder-copilot:latest
```

**Check health**:
```bash
curl http://localhost:8108/health
```

### Docker Compose

```bash
docker compose -f docker/docker-compose.builder-copilot.yml up -d
```

## Testing

Run the test suite:

```bash
# All tests
pytest tests/ -v

# Builder copilot-specific tests (if present)
pytest tests/agents/builder_copilot/ -v

# With coverage
pytest tests/ --cov=app.agents.builder_copilot --cov-report=html
```

## Logging

Builder Copilot produces two log streams:

- **Standard output**: Info and above (filtered at `PROGENITOR_LOG_LEVEL`)
- **Standard error**: Error and above
- **Files**: `<PROGENITOR_LOG_DIR>/builder_copilot/{stdout,stderr}.log` (rotating, 10MB per file)

Log format is configurable via `PROGENITOR_LOG_FORMAT` (text or json).

## Troubleshooting

### Issue: "Empty request. Describe the repository change to make."

**Cause**: The A2A message didn't contain text content.

**Solution**: Ensure your request includes a `Message` with non-empty `TextPart`:
```json
{
  "message": {
    "parts": [
      {
        "root": {
          "text": "Your request here"
        }
      }
    ]
  }
}
```

### Issue: Copilot CLI not found

**Cause**: `copilot` binary is not installed or not in PATH.

**Solution**:
```bash
# Install GitHub Copilot CLI
curl -fsSL https://gh.io/copilot-install | bash

# Or set explicit path
export PROGENITOR_COPILOT_BIN=/usr/local/bin/copilot
```

### Issue: Git worktree creation fails

**Cause**: Repository isn't properly initialized or permissions are incorrect.

**Solution**:
1. Verify `PROGENITOR_PROJECT_ROOT` is a valid git repository
2. Check permissions: `ls -la <repo>/.git`
3. Ensure git identity is set (see Configuration section)

### Issue: PR creation fails with "no changes"

**Cause**: Copilot didn't generate any changes, or changes are identical to main.

**Solution**:
1. Increase the specificity of your request
2. Use `python-search` skill explicitly to inspect the codebase first
3. Check logs: `tail -f <PROGENITOR_LOG_DIR>/builder_copilot/stdout.log`

## Contributing

### Adding a New Skill

1. Create `skills/<skill_name>/SKILL.md` with proper YAML front matter
2. Add allowed tools in the `allowed-tools` field
3. Write clear, concise instructions in the markdown body
4. Add to the `STEP_SKILLS` list in `agent.py` (if it's a step skill)

### Modifying the System Prompt

Update `SYSTEM_PROMPT` in `deep_agent.py`. The prompt is critical for task execution quality.

### Adding Tools

New tools are managed by `create_builder_copilot_tools()` in `tools.py`. See the builder-agent's tool implementations for patterns.

## Related Documentation

- [A2A Protocol](https://github.com/google/deepagent) — Agent-to-Agent communication
- [DeepAgent Docs](https://github.com/google/deepagent) — Building AI agents with skill files
- [GitHub Copilot CLI](https://docs.github.com/en/github-copilot/github-copilot-cli/using-github-copilot-cli) — Code generation tool
- [Builder Agent](../builder_agent/README.md) — Larger build orchestration agent
- [Progenitor](../../README.md) — Parent system

## Support

For issues, questions, or contributions:
1. Check the logs at `<PROGENITOR_LOG_DIR>/builder_copilot/`
2. Review skill files in `skills/` for execution guidelines
3. Verify environment configuration matches the Configuration section
4. File an issue with reproduction steps and logs

## License

MIT
