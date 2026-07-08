# Workflow / Kanban IPC Testing Playbook

How to exercise the kanban → workflow → alert pipeline end-to-end against a
live three-process stack, without burning tokens rediscovering endpoints each
time. Written after manually testing the `notify_user` alert workflow via IPC
(2026-07-08).

## Stack

Three processes, per `CLAUDE.md`. Check what's already running before
starting anything:

```bash
ps aux | grep -E "app.progenitor.main|uvicorn app.backend.main|vite" | grep -v grep
curl -s http://127.0.0.1:8000/health   # backend
curl -s http://127.0.0.1:5173/health   # frontend (proxies to backend)
```

Postgres: `postgresql://progenitor:progenitor@127.0.0.1:15433/progenitor`.
`psql` CLI is **not installed** in this environment — use `psycopg` directly
for raw row inspection:

```python
import asyncio, json, psycopg
async def main():
    conn = await psycopg.AsyncConnection.connect(
        "postgresql://progenitor:progenitor@127.0.0.1:15433/progenitor"
    )
    async with conn.cursor() as cur:
        await cur.execute(
            "SELECT value FROM store WHERE prefix='progenitor.state' AND key=%s",
            ("kanban_card:<id>",),  # or "workflow:<id>", "workflow_run:<id>", etc.
        )
        row = await cur.fetchone()
        print(json.dumps(row[0], indent=2, default=str) if row else "not found")
asyncio.run(main())
```

### Restarting a wedged runtime

The Azure LLM backend has, more than once, hung a model call indefinitely
with no further log output (see "Known instability" below). Recovery:

```bash
kill -TERM <progenitor_pid>; sleep 5; kill -KILL <progenitor_pid>  # if still alive
# relaunch:
cd /home/tama/source/progenitor/main && source .venv/bin/activate && export PYTHONPATH="$PWD"
nohup python -m app.progenitor.main > <logfile> 2>&1 & disown
```

The backend's IPC client (`IPCProgenitorClient`, `auto_reconnect=True`) does
**not** reliably reconnect to a freshly-restarted runtime in practice — it
logged `Failed to reconnect after 5 attempts` and stayed dead until the
backend itself was restarted too. If a ping via `/actions/run` 500s after a
runtime restart, kill and relaunch the backend the same way:

```bash
kill -TERM <backend_pid>; sleep 5; kill -KILL <backend_pid>  # if still alive
nohup python -m uvicorn app.backend.main:app --host 0.0.0.0 --port 8000 > <logfile> 2>&1 & disown
```

Always bound IPC calls with `curl --max-time N` — an un-timeboxed call can
hang the whole session waiting on a wedged runtime.

## Driving the agent over IPC (same path the frontend's quick chat uses)

```bash
curl -s --max-time 90 -X POST http://127.0.0.1:8000/api/ui/actions/run \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "<natural language instruction for the agent>",
    "invocation_type": "automation",
    "invocation_source": "<free-text tag for this test>",
    "widget_id": "<same tag>",
    "broadcast_response": true
  }'
```

This is a *real* agent turn — it goes through the full DeepAgent graph
(skills middleware, memory middleware, tool calls, model calls). Expect
5–60s for a simple turn. It is **not** backgrounded automatically; if it
runs long, the bash tool may background it on its own — check
`TaskOutput`/`TaskStop` rather than re-issuing the same call.

The agent's **only** mechanism for kanban work is the generic file tools
(`read_file`/`write_file`/`edit_file`/`ls`) against `/state/tasks/` — there
is no dedicated kanban tool, so prompts must say so explicitly or the agent
will try (and fail) to route to a subagent via `call_agent`.

### Kanban card frontmatter — exact field names

`/state/tasks/<id>.md`:

```markdown
---
title: Some title      # required, only hard-validated field
column_id: backlog     # NOT `column` — that key is silently ignored, card
                        # defaults to backlog. Cost me a full debug cycle.
status: idle
owner: user
---

Description body goes here.
```

Valid `column_id` values (fixed 5-stage pipeline, see
`_kanban_default_columns()` in `app/backend/ui_routes.py`): `backlog`,
`plan`, `build`, `validate`, `done`.

Get a fresh id first: ask the agent to call `generate_id` with
`prefix="card"` (or `POST /api/ui/kanban/cards` mints one for you — see
below).

## Kanban REST endpoints (backend, `/api/ui` prefix)

| Action | Method + path | Notes |
| --- | --- | --- |
| List cards | `GET /tasks` | Lightweight view, used by Tasks page |
| Full board | `GET /kanban` | Includes column metadata |
| Create | `POST /kanban/cards` | Body: `{title, summary, description, owner, project_id, labels}`. Always lands in `backlog`. |
| Move / patch | `PATCH /kanban/cards/{card_id}` | Body: `{column_id: "done", ...}`. Fires `kanban.card.moved` (and other) workflow triggers via the reconciler either way now — see bugs-fixed note below. |
| Delete | `DELETE /kanban/cards/{card_id}` | 204 |

## Workflow REST endpoints

| Action | Method + path | Notes |
| --- | --- | --- |
| List | `GET /workflows` | Summary cards |
| Read one | `GET /workflows/content?path=<id>` | Query param is `path`, but a bare workflow id resolves fine |
| Write/replace one | `PUT /workflows/content` | Body: `{path, workflow: {...}}`. **Must include `created_at` and `updated_at`** even on an update — full-document replace, not a patch. |
| Runs | `GET /workflow-runs?workflow_id=<id>` | Full run history incl. `trigger_event` payload and `last_output` |
| Pause / resume / trigger | `POST /workflows/{id}/pause` (or `/resume`, `/trigger`) | |

Node/trigger schemas for building a definition via `structured_output`:
`data/schemas/definition.workflow.schema.v1.json` (whole doc, self
contained) or the per-kind `data/schemas/node_*.workflow.schema.v1.json` /
`trigger.workflow.schema.v1.json` files for scoped edits. See
`data/skills/progenitor/managing-workflows/SKILL.md`.

### Standing test workflow

`workflow_e52003c8ee54` — "Notify When Kanban Card Moves to Done". Currently
configured: trigger `kanban.card.moved`, `to_column: done`, single `user`
node with `message_type: notify_user`, `level: success`. Reuse this rather
than creating a new one for alert/notify smoke tests.

## Alerts

`GET /api/ui/alerts` — **in-memory only** (`app/backend/alerts.py`, module
list, not persisted), wiped on every backend restart. After a `notify_user`
tool call fires, there's an async round trip (`_handle_alert_request` in
`app/backend/main.py` stores the alert, then kicks off a *second* IPC call
to have the agent summarize it) — poll a couple of times over ~10s rather
than checking once immediately after the triggering call returns.

## Bugs found and fixed (2026-07-08)

1. **Agent-native kanban writes didn't fire workflow triggers or live UI
   updates.** `_dispatch_workflow_triggers` (and the UI broadcast) used to
   fire only from the three REST handlers (`app/backend/ui_routes.py`
   create/patch/delete). The runtime's actual day-to-day mechanism for the
   agent to touch cards — `KanbanTaskDirectoryBackend` writing straight to
   the Postgres store via `StateManager` — bypassed all of it.

   **Fix**: `_reconcile_kanban_card_triggers` (`app/backend/ui_routes.py`)
   is now registered as a second, always-on `StateReader` watcher
   (`_queue_kanban_trigger_reconciliation`, wired in
   `start_state_status_watcher`, independent of the UI-only
   `_queue_state_watch_status` path so it still runs with no browser
   connected). It diffs the board against `_last_known_kanban_cards` (an
   in-memory last-dispatched-state cache, seeded from the live board on the
   first reconciliation pass after a backend restart) and fires the same
   `kanban.card.{created,updated,moved,status_changed,deleted}` triggers a
   REST-driven edit would. `_kanban_card_trigger_lock` (an `asyncio.Lock`)
   plus the REST handlers updating the same cache after their own dispatch
   prevents a REST-driven change from being double-dispatched when its own
   resulting NOTIFY reaches the reconciler. Covered by
   `tests/backend/test_kanban_trigger_reconciliation.py`.

2. **Azure Responses API calls 400'd whenever an MCP tool was registered.**
   `Missing required parameter: 'tools[0].name'` — hit via `call_agent`'s
   LLM-based auto-routing (`_route_agent` in
   `app/progenitor/tools/call_agent.py`), confirmed as a real, actively-used
   path (not a dead one): also the code path `structured_output`
   (`app/progenitor/structured.py`) uses, i.e. the mechanism the
   `managing-workflows`/`managing-schedules` skills lean on.

   Root cause: `normalize_mcp_tool_definition`
   (`app/progenitor/mcp/tool_schema.py`) returned the older Chat Completions
   nested shape (`{"type": "function", "function": {"name": ...}}`), but
   both `app/progenitor/llm/client.py` and
   `app/agents/builder_agent/llm/client.py` send tool definitions straight
   to the newer `/responses` endpoint, which wants a flat shape
   (`{"type": "function", "name": ...}`). Fixed by flattening
   `normalize_mcp_tool_definition`'s output and updating both clients'
   `_merge_tools` MCP-loop name extraction to match
   (`normalized.get("name")` instead of
   `normalized.get("function", {}).get("name")`).

   Separately observed (not yet root-caused): a plain model-call turn once
   hung with zero further log output for 10+ minutes, recovered only by
   killing and relaunching the runtime. Unclear if this was the same
   underlying issue manifesting as a hang instead of a clean 400, or
   something unrelated — worth watching for recurrence now that the 400 is
   fixed.

## Bugs found and fixed (2026-07-08, second pass)

Reproduced by asking the agent to create a workflow via `managing-workflows`
(`POST /api/ui/actions/run`, same pattern as above) repeatedly right after a
fresh runtime restart. Two independent, unrelated bugs both live in
`app/progenitor/llm/client.py`'s `structured_output`/`chat_structured_json`
path — the *only* LLM call path in a turn that doesn't go through the
separate LangChain `ChatOpenAI` client used for the main conversation loop
(`app/progenitor/agent/adapters.py::create_langchain_model`), so it's the one
place these show up:

1. **First `structured_output` call after any idle gap intermittently
   failed with `httpx.ConnectError: [Errno -3] Temporary failure in name
   resolution`.** The custom `LLMClient.client` (raw `httpx.AsyncClient`) is
   idle for most of a turn — only `structured_output`/`call_agent`
   auto-routing use it, unlike the main model loop's separate LangChain
   client which is warm throughout. httpx's default `keepalive_expiry` is
   5s, so the next request after any gap opens a fresh connection, and in
   this dev environment that fresh DNS+connect intermittently fails once
   then succeeds on immediate retry. Previously unhandled: the agent
   happened to retry the tool call itself after seeing the error, papering
   over it non-deterministically — a script or a less persistent agent would
   see "workflow creation failed."

   **Fix**: construct both `LLMClient.client` instances (progenitor and
   `app/agents/builder_agent/llm/client.py`, the duplicated implementation)
   with `transport=httpx.AsyncHTTPTransport(retries=2)`, which transparently
   retries connection-establishment failures (DNS/TCP/TLS, never a
   partially-sent request) before surfacing an error.

2. **`structured_output` 400'd with `"schema must be a JSON Schema of
   'type: \"object\"', got 'type: \"None\"'"` on some calls.** Two
   compounding bugs in the schema-closing helpers:
   - `_close_object_schemas` unconditionally overwrote `additionalProperties`
     with `False` whenever a node's `"type" == "object"` — including when
     `additionalProperties` was itself a *schema* (the value shape for an
     arbitrary-key map, e.g. `graph.nodes` in
     `definition.workflow.schema.v1.json`, keyed by generated node ids). That
     produced a self-contradictory closed schema (`minProperties: 1` but no
     properties allowed at all) that Azure's strict-mode preprocessor
     rejects.
   - `_build_json_schema_format_from_dict` never enforced a top-level
     `"type"` key. Plain JSON Schema treats a root schema with only
     `properties`/`required` as implicitly object-shaped, but the provider
     requires it explicit — an ad hoc inline schema (the `structured_output`
     tool's `json_schema` argument, as opposed to `schema_file`) missing it
     hits exactly this 400.

   **Fix**: `_close_object_schemas` now only clobbers `additionalProperties`
   when it isn't already a dict (and recurses into it when it is, so the
   nested node-kind schemas still get properly closed);
   `_build_json_schema_format_from_dict` now does
   `schema.setdefault("type", "object")` before the strict-mode pass, for
   both strict and non-strict callers. Covered by three new tests in
   `tests/progenitor/test_llm_client_extraction.py`.

## Cleanup after a test run

Test cards and workflow edits land in the user's real board/workflow list —
delete test cards (`DELETE /kanban/cards/{id}`) and revert workflow edits
that weren't intentional improvements when done.
