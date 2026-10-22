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

## Configuration
Model connections are ordinary database-backed application settings, managed in **Settings → Providers**:
- Foundry and OpenCode Go use the Responses API; Go accepts only the supported Responses model IDs.
- `/config/profile.json` selects a `provider_id` and `deployment`, or inherits the default connection when `provider_id` is absent. Never put endpoints or API keys in agent state.
- Credentials use encrypted database storage (`PROGENITOR_PROVIDER_ENCRYPTION_KEY` shared by backend/runtime) or an explicit provider API-key environment reference.
- `AZURE_OPENAI_BASE_URL` and `PROGENITOR_MODEL_DEFAULT` are imported once for existing installations, not ongoing runtime overrides.
- Saved provider changes apply on the next turn; active turns retain their connection snapshot.

## Registered Agents

- **Object agents** are local conversational subagents defined by manifests. User-owned manifests under `/agents/user/<name>/SYSTEM.md` are writable; bundled and plugin-provided manifests are read-only.
- **A2A agents** are remote peers registered in `/config/agents.json`; configure them through the registry rather than assuming a local port, card path, or process.
- **ACP agents** are supervised external sessions whose completion is fed back to the originating conversation by the heartbeat.

Agent permissions and mounts constrain the virtual documents exposed to an object agent. Give only the required skill, tool, and path access.

## Read-only Source Assets

`/self/`, `/repos/`, `/logs/`, `/schemas/`, and native `/skills/` are read-only mounts. Native skills come from static `data/skills/`; installed marketplace skills are also exposed read-only. Runtime-writable content belongs in the supported virtual-document domains above, not in source assets.

## Key Implementation Areas

- `src/progenitor/orchestration/graphs/progenitor_graph.py` — conversation graph construction
- `src/progenitor/orchestration/graphs/heartbeat_graph.py` — queue, cycle, and idle-heartbeat orchestration
- `src/progenitor/agent/` — DeepAgent setup and virtual filesystem backends
- `src/progenitor/state/` — state lifecycle facade over domain stores
- `src/progenitor/persistence/` — shared native store, checkpoints and notifications
- `src/progenitor/orchestration/state.py` — composed graph state; domain models live beside their owners
- `src/progenitor/a2a/`, `src/progenitor/acp/`, and `src/progenitor/async_subagents/` — tracked external work
