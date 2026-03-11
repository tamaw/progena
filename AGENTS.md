# Progenitor — Agent Registry

## Table of Contents

1. [Overview](#overview)
2. [Agent Registry](#agent-registry)
3. [Capabilities](#capabilities)
4. [Async vs Sync Execution Model](#async-vs-sync-execution-model)
5. [Logging Locations](#logging-locations)
6. [Deployment Notes](#deployment-notes)

---

## Overview

## Structure

Repository layout (relevant paths):
- `app/agents/` — per-agent services (builder_progenitor, builder_agent, observe_agent)
- `app/agents/builder_agent/tools/` — embedded components (e.g. builder_copilot)
- `app/data/` — persistent state, checkpoints, config
- `app/logs/` — default log directory referenced below

## Overview

This document registers all A2A agents in the Progenitor system. Progenitor implements the "Last Algorithm" dual-loop pattern: an outer perpetual loop (orchestrated by the core Progenitor runtime) delegates build work to inner-loop agents via the [A2A protocol](https://github.com/google/A2A) over HTTP/JSON-RPC 2.0. Each agent below is a standalone FastAPI/uvicorn service exposing an A2A endpoint, except `builder_copilot`, which is an embedded integration component.

---

## Agent Registry

| Agent | Port | Source | Primary Responsibility |
|---|---|---|---|
| `builder_progenitor` | 8108 | `app/agents/builder_progenitor/` | Orchestrates Copilot CLI to implement code changes end-to-end |
| `builder_agent` | 8107 | `app/agents/builder_agent/` | Inner-loop LangGraph executor (OBSERVE → PUBLISH cycle) |
| `observe_agent` | 8080 | `app/agents/observe_agent/` | Read-only health and diagnostic monitoring |
| `builder_copilot` | — | `app/agents/builder_agent/tools/copilot.py` | Embedded Copilot ACP integration (not a standalone service) |

---

## Capabilities

### builder_progenitor
- Receives A2A tasks; orchestrates full code-change workflow
- Creates and manages Git worktrees (`create_worktree`, `cleanup_worktrees`)
- Invokes GitHub Copilot CLI via Agent Client Protocol (ACP)
- Validates output: `pytest`, `ruff`, `pyright`
- Commits changes and opens GitHub PRs via `gh` CLI
- Searches codebases via ripgrep (`search_codebase`)

### builder_agent
- Full inner-loop LangGraph graph: OBSERVE → THINK → PLAN → BUILD → EXECUTE → MERGE → VERIFY → LEARN → PUBLISH
- Per-build SQLite checkpointing (`build_checkpoints.db`) for interruption recovery
- Discovers and invokes registered A2A agents via `ToolRegistry`
- Role-specific LLM model overrides (planner, builder, summary)
- Configurable max iterations to prevent infinite loops

### observe_agent
- Stateless read-only agent; never writes to state or logs
- `progenitor_health_report`: overall system health summary
- `diagnose_last_error`: extract and summarize recent errors from logs
- `tail_state_and_logs`: read current state and recent log entries
- Suitable for horizontal scaling (no shared write state)

### builder_copilot
- Embedded in `builder_progenitor` and `builder_agent`; not deployed independently
- Wraps GitHub Copilot CLI (`gh copilot` extension) via `agent-client-protocol` SDK
- Manages Copilot subprocess lifecycle or connects to a remote ACP TCP proxy (`COPILOT_ACP_URL`)
- Streams token output; writes per-call audit records to `llm_requests.jsonl`
- Configurable model, timeout, and extra flags per invocation

---

## Async vs Sync Execution Model

All agents are **fully async** (FastAPI + asyncio). Key coordination patterns:

- **Progenitor → builder_agent / builder_progenitor**: fire-and-forget A2A call; the outer loop polls or receives streamed `TaskStatusUpdateEvent` responses.
- **builder_agent inner loop**: LangGraph executes nodes sequentially within a single async task; each node is `await`ed before the next starts.
- **builder_copilot**: invoked as an async subprocess or TCP connection; results stream back via ACP before the calling node continues.
- **observe_agent**: request/response; completes synchronously within the A2A handler (no background tasks).
- **Progenitor core ↔ Backend API**: IPC via Unix socket or TCP (port 9500); JSON-RPC, used exclusively by the backend to send user messages into the outer loop.
- **Agent discovery**: resolved at startup from `/app/data/config/agents.json`.

---

## Logging Locations

Paths below reflect defaults; override with `PROGENITOR_LOG_DIR`.

| Agent | Log files | Notes |
|---|---|---|
| `builder_progenitor` | `/app/logs/builder/builder_progenitor.log`, `/app/logs/builder/llm_requests.jsonl` | Rotating (10 MB, 5 backups); JSONL LLM audit trail |
| `builder_agent` | `/app/logs/builder_agent/builder_agent.log`, `/app/logs/builder_agent/llm_requests.jsonl` | Same rotation policy; SQLite checkpoints at `/app/data/state/build_checkpoints.db` |
| `observe_agent` | FastAPI access log to stdout only (no file handler by default) | Reads logs from other agents via read-only mounts |
| `builder_copilot` | Writes into the host agent's `llm_requests.jsonl` | No separate log file; embedded component |

Log level: `PROGENITOR_LOG_LEVEL` (default `info`). Format: `PROGENITOR_LOG_FORMAT` (`text` or `json`).

---

## Deployment Notes

### Required environment variables (all builder agents)

| Variable | Purpose |
|---|---|
| `AZURE_OPENAI_BASE_URL` | Azure OpenAI endpoint |
| `AZURE_OPENAI_API_KEY` | Azure authentication |
| `GH_TOKEN` | GitHub CLI auth (PR creation) |
| `COPILOT_GITHUB_TOKEN` | GitHub Copilot CLI auth |
| `PROGENITOR_REPO_URL` | Git repo cloned into worktrees root |
| `GIT_AUTHOR_NAME` / `GIT_AUTHOR_EMAIL` | Commit identity |

### Per-agent extras

- **builder_progenitor**: `PROGENITOR_COPILOT_BIN` (binary path), `PROGENITOR_COPILOT_TIMEOUT_SECONDS` (default 1200), `PROGENITOR_COPILOT_ADDITIONAL_FLAGS`
- **builder_agent**: `COPILOT_ACP_URL` (remote ACP proxy), `TEST_MODE=true` disables real Copilot calls
- **observe_agent**: `OBSERVE_STATE_DIR` (default `/app/data/state`), `OBSERVE_LOGS_DIR` (default `/app/logs`); mount both as **read-only**

### Persistent volumes

`/app/data` (state + SQLite), `/app/logs` (log files), `/app/worktrees` (Git workspaces)

### Scaling

`builder_progenitor` and `builder_agent` use file-based state; run as **single instances** per deployment. `observe_agent` is stateless and can be scaled horizontally behind a load balancer.

### Required services

Azure OpenAI (or OpenAI API), GitHub Copilot CLI (`gh extension install github/gh-copilot`), GitHub CLI (`gh`), Git ≥ 2.5 (worktree support).

### Owner / contacts

See repository maintainers listed in `pyproject.toml`.
