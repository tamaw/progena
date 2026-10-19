# ACP Agents

Progenitor can invoke **ACP agents** — local CLIs that speak the **Agent Client
Protocol** (Zed's stdio JSON-RPC standard), the most prominent being the GitHub
Copilot CLI (`copilot --acp`). ACP is a peer protocol to A2A: where A2A agents are
remote HTTP servers, ACP agents are local subprocesses Progenitor spawns and drives.

> Terminology: this is the **Agent Client Protocol** (the `agent-client-protocol`
> Python SDK, imported as `acp`), *not* IBM's "Agent Communication Protocol" (which
> merged into A2A). It is also the inverse of LangChain's `deepagents-acp`, which
> exposes a deep agent *as* an ACP server.

## How it fits together

```
acp_* tools / workflows ─→ acp_task:<id> document (queued or updated)
                                   │ document event
backend ACPTaskSupervisor ─────────┘
   └─ ACPRunner (app/progenitor/acp/) ─→ ACP agent (TCP server or stdio CLI)
```

The runtime registry is database-backed: `ToolRegistry` reads and writes the
Postgres `registry` document through `StateManager`. ACP agents are registered
as `acp_agent` entries with `acp={command|host+port, profile, model, cwd, ...}`.

### Interactive sessions

Every session is an `acp_task:<id>` document. The backend
`ACPTaskSupervisor` is the sole driver and keeps the connection that owns the
running turn, which is required for live steering and replies.

- **Start** — `acp_start(agent, prompt, cwd=None, card_id=None, mode=None)`
  creates the task. `mode` may be `default`, `acceptEdits`, `plan`, or
  `bypassPermissions`; it is applied when starting/resuming and when changed
  during a turn.
- **Send** — `acp_send(task_id, message)` steers a running Claude turn on that
  live connection (`outcome: "injected"`). When idle it starts the next turn on
  the same session (`outcome: "started_turn"`). Use short, specific corrections;
  do not cancel merely to send a follow-up.
- **Inspect** — `acp_status(task_id, wait_seconds=0)` returns the task snapshot;
  its wait is capped at 300 seconds. `acp_list(card_id=None, status=None,
  agent=None, limit=20)` finds sessions.
- **Answer** — Claude questions and plan submissions appear in `pending_input`.
  Call `acp_answer(task_id, input_id, answer)` promptly: Claude is blocked until
  the supervisor sends the matching `answers[input_id]` reply on its live
  connection and clears `pending_input`. Question answers are form-value objects
  (or `{"action":"decline"}`); plan answers are
  `{"approve": bool, "feedback"?: string}`.
- **Cancel** — `acp_cancel(task_id)` sets `cancel_requested`; use it only for a
  turn that is going wrong.

The supervisor processes pending `messages` in order, using steering while a
turn runs and a normal next turn when idle. It sets their `status` to
`injected`, `started_turn`, or `failed`, with send metadata. Ordinary tool
permissions are auto-approved; plan mode's ExitPlanMode is exposed as a
`pending_input` of kind `plan` for explicit approval or feedback.

Workflow event nodes can react to `acp_task` `status`, `stalled`, and
`pending_input` changes. The supervisor owns status, progress, output,
`pending_input`, and session fields; requesters own messages, mode, answers,
and cancellation requests. A task cannot start beyond `PROGENITOR_ACP_MAX_TURNS`.

ACP agents are reached only through `acp_*`; `a2a_*` tools handle remote A2A agents.

## A2A task sessions

Remote HTTP A2A agents use a separate task-session family:

- `a2a_send(agent, message, ...)` sends a task or follow-up and waits up to
  `wait_seconds` (default 60, maximum 300) for a terminal or `input-required`
  state. It returns the tracked task record even when the wait expires.
- `a2a_send_async(agent, message, ...)` returns the tracked task immediately
  and queues a completion follow-up turn.
- `a2a_status`, `a2a_cancel`, and `a2a_list` refresh, cancel, and enumerate
  tracked A2A tasks. `a2a_send` and `a2a_status` return
  `build_task_response` records, including persisted `progress_log` entries.

The A2A tools reject ACP-transport registry entries; use `acp_start` instead.

## Registering an ACP agent

No ACP agents are seeded by default — register the ones you want with the
`register_acp_agent` tool, which persists to the database. The simplest form is a
**connection string**:

- `tcp://192.168.0.2:3000?model=claude-haiku-4.5&cwd=/tmp` — connect to a running
  `copilot --acp --port 3000` server (TCP; the primary mode).
- `stdio://copilot` or a bare `copilot` — spawn a local stdio agent.

Explicit fields (`host`/`port`/`command`/`profile`/`model`/`cwd`) override anything
derived from the connection string. A known `profile` (e.g. `"copilot"`) supplies
stdio CLI flags and the `--yolo`/model behaviour; otherwise pass raw `args`.

Remove an agent with the `unregister_agent` tool (by name) — it deletes any agent
(ACP or A2A) from the database registry. The A2A counterpart for *adding* agents is
`register_a2a_agent` (by URL).

## Working directory & safety

Each task runs in its `cwd` (a path on the agent's host), else the registry
entry's `acp.cwd`, else `/tmp`. For build work pass a worktree the `repo-manager`
agent created under `/app/repos` (`PROGENITOR_WORKTREE_DIR`): it is mounted at
the same path in the runtime, backend, and Claude containers, all running as
UID 10001, so the path the agent reports is the one Claude edits.

Permission requests are auto-approved (an allow option is chosen) — **the
working directory is the guardrail, not per-call prompting**. Claude has no
GitHub credentials; pushing and pull requests are the `repo-manager` agent's job
(`git_publish: true`), using the runtime's `GH_TOKEN`.

## Restarts

On backend startup the supervisor marks tasks left `working` as `interrupted`
(their connection died with the process) and starts tasks left `queued`. On
shutdown in-flight turns are cancelled and marked `interrupted`. Claude keeps
sessions on disk (`/home/claude/.claude`), so queueing a new prompt on an
interrupted task resumes the same conversation.

## Limitations

- New ACP agents are managed via the `register_acp_agent` tool / config edits — no
  backend REST API or Agents-page form yet.
