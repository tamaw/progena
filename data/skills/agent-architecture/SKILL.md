---
name: agent-architecture
description: Understand Progenitor's conversation graph, heartbeat runtime, durable virtual documents, and registered-agent model. Use when explaining or extending the runtime, or diagnosing agent behavior and state.
---

# Agent Architecture

## When to Use
- Questions about how Progenitor processes a conversation or background work
- Debugging runtime behavior, persistent state, or agent registration
- Extending the conversational agent, heartbeat graph, or virtual-document model

## Runtime Model

Progenitor has two cooperating graphs, not a build loop:

- **Conversation graph** — the user-facing DeepAgent graph. It handles a chat turn, tool calls, memory, and delegation to configured agents.
- **Heartbeat graph** — repeatedly dequeues input and, when idle, runs due maintenance. Its startup and idle work includes MCP and registry refresh, schedules, tracked A2A/ACP/async-subagent completion handling, health updates, and other periodic maintenance. A queued input runs one conversation cycle, then the runtime records a checkpoint and health state before cleanup.

The conversation graph is the source of chat behavior. The heartbeat provides queueing, background work, and lifecycle orchestration; it does not run a separate plan/build/merge workflow.

## Durable State and Virtual Documents

The source of truth is the Postgres-backed state store. Conversation messages live in LangGraph checkpoints; per-thread `current_state` documents retain scalar cycle and response bookkeeping. Do not look for a general `/data/` mount or edit local JSON files.

The agent uses virtual paths, validated on write where applicable:

- `/config/agents.json` — registered A2A peers and runtime tools.
- `/config/mcp.json` and `/config/mcp_tools.json` — MCP servers and tool enablement.
- `/config/schedule.json`, `/config/plugins.json`, `/config/marketplaces.json`, `/config/preferences.json`, and `/config/profiles.json` — editable runtime configuration.
- `/config/health.json` and `/config/repo_scan.json` — heartbeat-owned, read-only status documents.
- `/config/repos/<id>.json` — repository declarations; live repository state is in `repo_scan.json`.
- `/tasks/board.json` and `/tasks/<id>.md`, `/workflows/<id>.json`, `/routines/<id>.json`, and `/policies/<id>.json` — persisted work-management and execution definitions.
- `/sessions/<date>_<title-slug>_<id8>.md` — chat records; only `title`, `pinned`, and `archived` front-matter fields are editable.
- `/memories/` and `/knowledge/<base-id>/` — persistent memories and catalogued knowledge pages.
- `/instructions/` — validated stored instruction documents. Checked-in instruction files seed missing documents only; existing stored instructions remain user-owned.

Use `read_file`, `write_file`, and `edit_file` against these virtual paths. The backend validates supported writes and publishes the resulting state change; paths are not local filesystem locations.

## Registered Agents

- **Object agents** are local conversational subagents defined by manifests. User-owned manifests under `/agents/user/<name>/SYSTEM.md` are writable; bundled and plugin-provided manifests are read-only.
- **A2A agents** are remote peers registered in `/config/agents.json`; configure them through the registry rather than assuming a local port, card path, or process.
- **ACP agents** are supervised external sessions whose completion is fed back to the originating conversation by the heartbeat.

Agent permissions and mounts constrain the virtual documents exposed to an object agent. Give only the required skill, tool, and path access.

## Read-only Source Assets

`/self/`, `/repos/`, `/logs/`, `/schemas/`, and native `/skills/` are read-only mounts. Native skills come from static `data/skills/`; installed marketplace skills are also exposed read-only. Runtime-writable content belongs in the supported virtual-document domains above, not in source assets.

## Key Implementation Areas

- `app/progenitor/graph/progenitor_graph.py` — conversation graph construction
- `app/progenitor/graph/heartbeat_graph.py` — queue, cycle, and idle-heartbeat orchestration
- `app/progenitor/agent/` — DeepAgent setup and virtual filesystem backends
- `app/progenitor/state/` — state models and persistence
- `app/progenitor/a2a/`, `app/progenitor/acp/`, and `app/progenitor/async_subagents/` — tracked external work
