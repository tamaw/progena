# Builder Copilot

## 1) Title and Overview

Builder Copilot is the repository mutation agent in this codebase. It runs as an A2A-compatible FastAPI service (`app/agents/builder_copilot/agent.py`), executes changes inside isolated git worktrees, uses GitHub Copilot CLI for implementation, and validates work through explicit test/lint commands before creating a pull request.

## 2) Purpose

Builder Copilot exists to turn natural-language change requests into small, reviewable PRs while preserving repository safety:

- isolate edits in worktrees
- keep a predictable step order (`plan -> build -> test -> lint -> pr`)
- prefer focused validation before publish
- expose a protocol-friendly interface for orchestration by Progenitor and other clients

## 3) Core Capabilities

- **A2A service surface** via `a2a-sdk` (`app/agents/builder_copilot/agent.py`)
- **Reasoning-backed preflight planning** via Azure Responses (`app/agents/builder_copilot/client.py`)
- **DeepAgent execution graph** with middleware, tools, and skills (`app/agents/builder_copilot/deep_agent.py`)
- **Worktree lifecycle** via shared git tooling (`create_worktree`, `commit_changes`)
- **Implementation via Copilot ACP** (`run_copilot_build`, `build_progenitor`)
- **Command execution in worktree** for test/lint/build validation (`run_worktree_command`)
- **Python code search** using ripgrep (`python_search`)
- **PR creation via gh CLI** (`create_pull_request`)

## 4) Skills Overview

Builder Copilot advertises these step skills in `STEP_SKILLS` (`app/agents/builder_copilot/agent.py`), with detailed skill files under `app/agents/builder_copilot/skills/`.

### `plan`
- **Purpose:** Normalize request scope, create/confirm worktree, and define validation path.
- **When used:** At the beginning of a new task.
- **Key tools:** `summarize_request`, `create_worktree`, `python_search`, `run_worktree_command`.

### `build`
- **Purpose:** Implement requested code changes.
- **When used:** After planning is complete.
- **Key tools:** `run_copilot_build`, `build_progenitor`, `create_worktree`, `python_search`.
- **Notes:** This step is enforced by system prompt/order in `deep_agent.py`; there is no separate `skills/build/SKILL.md` file in the current tree.

### `test`
- **Purpose:** Run focused tests relevant to changed areas.
- **When used:** After build, before lint/PR unless explicitly skipped.
- **Key tools:** `run_worktree_command`.

### `lint`
- **Purpose:** Run lint/format checks and fix issues before publish.
- **When used:** After test.
- **Key tools:** `run_worktree_command`.

### `pr`
- **Purpose:** Commit validated changes and open a PR.
- **When used:** After build/test/lint gates are satisfied or explicitly waived.
- **Key tools:** `commit_changes`, `create_pull_request`, `run_worktree_command`.

### `python-search`
- **Purpose:** Quickly locate Python symbols, imports, tests, and references before editing.
- **When used:** During plan/build when code context is needed.
- **Key tools:** `python_search` (ripgrep-backed), optionally `grep`/`execute` as listed by skill metadata.

## 5) Execution Workflow (`plan -> build -> test -> lint -> pr`)

Builder Copilot is configured to follow this order by default (`SYSTEM_PROMPT` in `app/agents/builder_copilot/deep_agent.py`):

1. **plan**: normalize request, establish worktree, inspect code
2. **build**: apply minimal changes with Copilot/file tools
3. **test**: run focused tests in the worktree
4. **lint**: run lint/format checks in the worktree
5. **pr**: commit, push, and open PR

Preflight planning is performed first in `BuilderCopilotService.process()` via `BuilderCopilotClient.plan_step()` and injected into the execution prompt.

## 6) Configuration

Configuration is loaded from environment variables (`app/agents/builder_copilot/config.py`, `.env.example`).

### Environment variables

Common Builder Copilot variables:

```bash
# LLM / Azure Responses
AZURE_OPENAI_BASE_URL=https://<resource>.openai.azure.com/openai/v1
AZURE_OPENAI_API_KEY=<key>
PROGENITOR_MODEL_DEFAULT=gpt-5.2-chat
PROGENITOR_MODEL_PLANNER=<optional>
PROGENITOR_MODEL_BUILDER=<optional>

# Copilot CLI
PROGENITOR_COPILOT_BIN=copilot
PROGENITOR_COPILOT_TIMEOUT_SECONDS=1200
PROGENITOR_COPILOT_MODEL=gpt-5.3-codex
PROGENITOR_COPILOT_DEFAULT_FLAGS=--yolo
PROGENITOR_COPILOT_ACP_ALLOWED_DIRS=app/agents,app/mcp

# Storage/runtime paths
PROGENITOR_PROJECT_ROOT=<repo or mounted workspace>
PROGENITOR_WORKTREE_DIR=<worktree root>
PROGENITOR_STATE_DIR=<state dir>
PROGENITOR_LOG_DIR=<log dir>

# Git identity for commits
GIT_AUTHOR_NAME=<name>
GIT_AUTHOR_EMAIL=<email>

# Auth for PR push/create
GH_TOKEN=<token>
# or GITHUB_TOKEN / COPILOT_GITHUB_TOKEN depending on environment
```

### pyproject / pytest / lint assumptions

Repository defaults used by Builder Copilot validation commands:

- **Pytest:** `pytest.ini` and `[tool.pytest.ini_options]` in `pyproject.toml` (`asyncio_mode=auto`, tests under `tests/`)
- **Lint:** Ruff configured in `pyproject.toml` (`[tool.ruff]`, `[tool.ruff.lint]`)
- **Type checking:** Mypy configuration exists in `pyproject.toml` (`[tool.mypy]`)

Typical commands:

```bash
pytest
ruff check app
mypy app
```

### Docker references

- Builder Copilot container definition: `app/agents/builder_copilot/Dockerfile`
- Main stack compose file (Progenitor + backend IPC): `docker/docker-compose.progenitor.yml`

`Dockerfile` for Builder Copilot installs `git`, `gh`, `copilot`, `ripgrep`, mounts `/app/worktrees`, and serves `app.agents.builder_copilot.agent:app` on port `8108`.

## 7) A2A Protocol Invocation

Builder Copilot can be invoked either directly as an A2A agent service or indirectly through Progenitor orchestration.

### How invocation works

- **Direct A2A:** client calls Builder Copilot endpoint (`app/agents/builder_copilot/agent.py`) using A2A SDK request flow.
- **Via Progenitor:** Progenitor delegates to registered A2A agents using `call_agent` (`app/progenitor/tools/call_agent.py`), while backend/frontend communication uses Progenitor IPC (`app/progenitor/ipc/*`, `app/backend/ipc_client.py`).

### Example A2A request/response payloads

The exact wire envelope is provided by `a2a-sdk`; logical payloads are equivalent to:

```json
{
  "message": {
    "role": "user",
    "parts": [
      { "text": "Add a retry helper for IPC reconnect logic and cover it with tests." }
    ],
    "metadata": {
      "repo": "progenitor",
      "base_branch": "main"
    }
  }
}
```

```json
{
  "task_status": "completed",
  "artifacts": [
    {
      "name": "builder-copilot-result",
      "parts": [
        { "text": "Completed. Worktree: ... Commands: ... PR: https://github.com/..." }
      ]
    }
  ]
}
```

### Example IPC request/response (Progenitor transport)

IPC message schema is defined in `app/progenitor/ipc/protocol.py` (length-prefixed JSON over TCP):

```json
{
  "type": "user_message",
  "request_id": "req_123",
  "stream": true,
  "content": "Use Builder Copilot to refactor the scheduler backoff logic safely."
}
```

```json
{
  "type": "stream_chunk",
  "request_id": "req_123",
  "content": "I delegated to Builder Copilot and it opened PR ...",
  "is_final": true
}
```

Relevant modules:

- `app/progenitor/ipc/protocol.py` (message model/encoding)
- `app/progenitor/ipc/server.py` (IPC server, request routing, streaming)
- `app/progenitor/ipc/client.py` (IPC client, heartbeats, event handling)
- `app/backend/ipc_client.py` (backend adapter to Progenitor IPC)
- `app/progenitor/runtime/deep_agent_adapter.py` (runtime graph bridge)
- `app/progenitor/agent/deep_agent.py` (Progenitor DeepAgent factory)

## 8) Usage Examples

### Example: add a new feature

```text
Request: Add an endpoint that returns queue metrics and include tests.
Expected flow: plan -> build -> test -> lint -> pr
Outcome: New endpoint + tests + lint-clean PR URL.
```

### Example: fix a bug

```text
Request: Fix race condition in IPC streaming response handling.
Expected flow: plan reproducer -> build fix -> targeted tests -> lint -> pr
Outcome: Minimal patch, regression test, PR with failure/fix notes.
```

### Example: refactor code safely

```text
Request: Extract shared request parsing helper in builder_copilot.agent.
Expected flow: plan dependencies -> build refactor -> run unchanged tests -> lint -> pr
Outcome: Behavior-preserving refactor with focused validation evidence.
```

## 9) Architecture

### High-level diagram (ASCII)

```text
User/Orchestrator
      |
      v
+--------------------------+
| Builder Copilot A2A App  |  app/agents/builder_copilot/agent.py
|  - preflight planner     |
|  - deep agent execution  |
+------------+-------------+
             |
             v
+--------------------------+
| DeepAgent + Skills       |  deep_agent.py + skills/*
|  plan/build/test/lint/pr |
+------------+-------------+
             |
             v
+--------------------------+
| Tool Runtime             |  tools.py
|  - create_worktree       |
|  - run_copilot_build     |
|  - run_worktree_command  |
|  - python_search         |
|  - commit/create_pr      |
+------------+-------------+
             |
             v
+--------------------------+
| Git Worktree + Repo      |
| Isolated branch changes  |
+--------------------------+
```

### Relationship to Progenitor runtime

- Progenitor runs its own DeepAgent/runtime loop (`app/progenitor/runtime/loop.py`, `app/progenitor/agent/deep_agent.py`).
- Progenitor can delegate build tasks to A2A agents using `call_agent` and discovered agent cards.
- Backend talks to Progenitor through IPC (`app/progenitor/ipc/*`), and Progenitor may in turn invoke Builder Copilot.

### Worktree isolation model

- Each task gets an isolated git worktree (`create_worktree`) keyed by `work_item_id`.
- Build/test/lint/commit operations execute inside that worktree path.
- PR creation pushes the worktree branch, keeping main checkout clean.

### Tooling model

- **Copilot ACP:** `run_copilot_build` / `build_progenitor` run Copilot in scoped mode.
- **Shell validation:** `run_worktree_command` executes tests/lint/build commands in worktree.
- **Code discovery:** `python_search` performs fast symbol/reference search with ripgrep.

## 10) Development and Testing

### How to run tests

```bash
# Activate venv first
& .\.venv\Scripts\Activate.ps1

# Builder Copilot focused tests
pytest tests/agents/builder_copilot -v

# IPC + Progenitor integration tests
pytest tests/progenitor/test_ipc_protocol.py -v
pytest tests/progenitor/test_ipc_integration.py -v

# Full suite
pytest -v
```

### How to run lint

```bash
& .\.venv\Scripts\Activate.ps1
ruff check app
mypy app
```

### How to run locally

```bash
# Terminal 1: Builder Copilot service (A2A)
& .\.venv\Scripts\Activate.ps1
python -m uvicorn app.agents.builder_copilot.agent:app --host 0.0.0.0 --port 8108

# Terminal 2: Progenitor runtime (if testing delegation/IPC path)
& .\.venv\Scripts\Activate.ps1
python -m app.progenitor.main

# Optional backend for /v1/chat + IPC bridge
& .\.venv\Scripts\Activate.ps1
python -m uvicorn app.backend.main:app --host 0.0.0.0 --port 8000
```

## Repository Pointers

- Builder Copilot package: `app/agents/builder_copilot/`
- Builder Copilot skills: `app/agents/builder_copilot/skills/`
- Progenitor IPC: `app/progenitor/ipc/`
- Progenitor runtime bridge: `app/progenitor/runtime/deep_agent_adapter.py`
