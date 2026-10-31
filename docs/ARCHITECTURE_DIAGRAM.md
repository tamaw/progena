# Progena AI Runtime Architecture

This document describes the running stack. For the checked-in repository layout,
see [PROJECT_STRUCTURE.md](PROJECT_STRUCTURE.md). The normative browser-client
protocol is [BACKEND.md](BACKEND.md).

## High-level topology

```text
Browser frontend
      |
      | finite commands/queries
      | X-Operation-Id: <operation UUID>
      v
Backend HTTP API ---- internal HTTP/SSE ----> Progena runtime
      |                                           |
      | HTTP 202 Accepted acknowledgement         +--> runtime tools and integrations
      | { request_id, event_types }              |
      | (no application data)                    +--> persisted runtime state and logs
      |
      +--> GET /api/ui/stream ------------------> realtimeListener + Redux reducers
      |       event: ui.event / UiEventEnvelope          |
      |                                                  v
      |                                           Redux selectors -> pages/components
      |
      +--> GET /api/ui/responses/{request_id}/stream --> useChatApi live callbacks
              event: chat.response.*                     (active streaming chat only)
```

## Browser data flow

Application data moves in one direction, but on two SSE lanes with different
responsibilities:

- **Canonical lane:** typed `ui.event` envelopes populate Redux and carry app
  lifecycle/state, including chat response lifecycle.
- **Dedicated chat-content lane:** request-scoped `chat.response.delta` and
  `chat.response.event` frames feed the active live-turn renderer.

### Finite request flow

```text
frontend generates UUID
        |
        +-- X-Operation-Id --> retained finite route
                                      |
                         validate / persist or dispatch
                                      |
                       enqueue causal ui.event envelope(s)
                       payload.request_id = UUID
                                      |
                 HTTP 202 Accepted { request_id, event_types }
                                      |
                                      v
  GET /api/ui/stream: event: ui.event, data: UiEventEnvelope
                                      |
        Redux applies typed result and resolves only UUID's pending operation
```

The ordering is **persist/dispatch → enqueue causal canonical event(s) →
acknowledge**. The acknowledgement means enqueued, not delivered; it never
carries application state, generated output, log content, a download, or an
inline SSE response body. Events caused by other tabs still update Redux but do
not resolve this tab's pending operation.

### Streaming chat flow

```text
POST /api/ui/responses (stream:true)
        |
        +-- 202 { request_id, event_types:["chat.response.started"] }
        |
        +-- canonical /api/ui/stream -----------------> chat.response.started
        |                                              chat.response.completed|failed|cancelled
        |
        +-- dedicated /api/ui/responses/{request_id}/stream
               chat.response.delta
               chat.response.event
               chat.response.heartbeat
               chat.response.resync_required
```

The dedicated stream is keyed by the acknowledged `request_id`. It carries only
response-content transport for that request; canonical lifecycle ownership stays
on `/api/ui/stream`.

### Queued follow-ups

Runtime-owned `chat_queue:<conversation_id>` documents hold pending inputs,
ordering, delivery claims and bounded command deduplication. Queue commands cross
the backend gateway; snapshots return through canonical `chat.queue.updated`
events. The browser renders state and issues commands, not delivery decisions.

Ordinary inputs drain after the active turn under `ConversationTurnGate`.
**Send now** selects an input for native root middleware at the next model
boundary, after outstanding tools finish, without starting a competing graph run
or cancelling work. Stable human-message checkpoint IDs reconcile interrupted
claims. A request-scoped boundary event separates live transcript rows when one
invocation handles another user instruction. Deferred execution publishes its
actual lifecycle so open conversations can reconcile committed history.

## Stream lifecycle

### Canonical stream

The canonical stream uses `text/event-stream`; every application frame has SSE
event name `ui.event`. Its envelope has `id`, increasing `seq`, typed `type`,
`scope` (`global`, `session`, or `chat`), optional `session_id`, event-specific
`payload`, and ISO-8601 `timestamp`.

- A bounded, in-memory 200-event broadcast replay buffer supports
  `Last-Event-ID` reconnect replay in sequence order.
- `connection.ready` follows the available replay and marks the live boundary;
  it includes initial status and alerts but is not a correlated domain snapshot.
- Replay is not durable resynchronization. If a cursor is old or invalid, the
  frontend must request needed snapshots and wait for their correlated events.
- Idle connections receive `connection.heartbeat`; graceful close is the
  canonical `ui.event` envelope `connection.shutdown` with shutdown reason and
  message.
- Reducers deduplicate envelope IDs and are safe for replayed snapshots,
  updates, and deletes.

### Dedicated response stream

The dedicated response stream also uses `text/event-stream`, but it does not use
`UiEventEnvelope`. It serves direct response payloads on the SSE event names
`chat.response.delta`, `chat.response.event`, `chat.response.heartbeat`, and
`chat.response.resync_required`.

- Only `chat.response.delta` and `chat.response.event` are replayable.
- Replayable frame IDs are `chat-response:{request_id}:{seq}`.
- `Last-Event-ID` takes precedence over the `after` query parameter.
- Invalid or expired cursors emit one `chat.response.resync_required` frame and
  then close.
- Once canonical terminal state is reached, the stream closes after queued
  retained content frames have been delivered.

There is no `/api/ui/events` EventSource, no `/api/ui/status` EventSource, and
no response-body token SSE.

## Invocation entry points

- **Chat sessions:** chat metadata routes establish/select persisted chat
  sessions and publish canonical state envelopes.
- **Chat generation:** `POST /api/ui/responses` acknowledges the request,
  canonical `/api/ui/stream` publishes lifecycle, and dedicated
  `/api/ui/responses/{request_id}/stream` publishes content.
- **Automation, workflow, schedule, MCP, widget, and task operations:**
  retained operations acknowledge acceptance and publish typed canonical state
  events. Their UI data is never returned in the finite response body.
- **Messaging channels:** the backend's `ChannelManager` runs one adapter per
  enabled `channel:<id>` document (Telegram today). Inbound channel messages
  become runtime turns on per-chat threads (`tele_<chat_id>`); while no browser
  is connected, agent messages, questions and scheduled results are forwarded to
  channels that notify when away. Channel settings and live status reach the
  browser only as `channels.snapshot` events.

## Responsibilities

### Runtime

- Owns the long-running conversation graph, checkpoints, scheduled work, and
  runtime tools.

### Backend

- Owns finite browser routes plus both browser SSE transports.
- Publishes canonical typed events after state changes succeed.
- Retains bounded replay for canonical envelopes and per-request response
  content frames.

### Frontend

- Opens one canonical `EventSource` for `/api/ui/stream`.
- Opens request-scoped response `EventSource` instances only while a chat stream
  is active.
- Keeps Redux as durable application state and uses selector-driven pages.
- Treats HTTP acknowledgements as operation metadata, not data hydration.

## State and logging

Runtime and backend state, including the Progena state documents and
operational logs, are persisted in the configured Postgres stores. The
Progena agent receives selected documents through a validated virtual
filesystem (`/config/*.json`, task Markdown files under `/tasks/`, and
workflow JSON files under `/workflows/`); these are database-backed views, not
local files. State changes relevant to the browser are represented as typed
canonical stream events after persistence.

User and marketplace object-agent definitions use `agent:` content documents
in the runtime's Postgres state namespace. Bundled native agents stay in
`data/agents/objects/`, included in both runtime and backend images; they are
never imported into Postgres and cannot be edited or deleted through the app.
The composite filesystem exposes read-only native files through
`/agents/objects/` and `/agents/native/`, alongside Postgres-backed `/agents/`.
User manifests at `/agents/user/<name>/SYSTEM.md` support validated reads,
writes, and edits through the `managing-agents` skill. Front matter conforms
to `agent-manifest.schema.v1.json`; `structured_output` and `json_to_yaml`
provide the authoring path. Installed plugin manifests remain read-only in
this shared filesystem. Remote A2A registrations use `managing-a2a` and
`/config/agents.json` instead.
Restart after editing bundled files in development; disk edits have no NOTIFY.

Committed document changes flow through the shared store trigger and Postgres
LISTEN/NOTIFY. The runtime refreshes object-agent callable/task descriptors;
the backend publishes typed canonical roster/graph events. Routine deletion
removes the live document and its catalog entry, not immutable revisions or
checkpoint history. Reconnect reconciliation recovers missed changes.

Routine execution keeps structured child inputs/outputs separate from its
conversational task adapter. Each run has its own correlation/checkpoint scope;
delegated conversations continue only within that run, including human resume.
Each nested routine invocation resolves the live child document and records its
exact revision, child thread/session/request IDs, and parent thread/node in that
node's checkpointed `run_meta` entry. These records are execution history, not
dependency pins: a later invocation after a pause may use an edited child, while
earlier entries keep referencing their original immutable graph snapshots.
Recording a call does not edit the parent graph or create a parent revision.
The existing run-state SSE payload carries these records to the node inspector.
Routine run feedback uses the canonical UI stream, without discovery or state
polling. The stream's per-tab `client_id` identifies a page subscription set by
`PUT /api/ui/agent-profiles/{id}/run-subscription`. Subscribe before posting a
run; leaving the editor removes detail interest, while execution continues.
Committed root routine checkpoints notify on `routine_checkpoints`; the backend
reconstructs native checkpoint channels only for watched runs and sends detail
to those connections. Detail frames never enter global replay, and a changed
subscription also drops already-queued detail for the old selection.

Immutable `routine_run_event:` documents record enqueued, started, paused,
completed, and failed transitions. Their store notifications publish small
global lifecycle events independently of page subscriptions, supporting
background indicators and completion/error notifications. `session_id` scopes
the checkpoint thread; `request_id` identifies each attempt, including resumes
of the same interactive thread. Explicit pause/terminal events determine UI
status. SSE reconnect restores compact lifecycle state and re-registers the
viewed run's detail subscription with an initial snapshot; history remains an
on-demand finite request. No conversation stream is needed for routine runs.

Human-node detail includes native interrupt IDs, questions, configured responses,
and any submitted answer claim. The existing inspector renders those responses.
An approval POST accepts the exact thread and interrupt, returns 202, and queues
a direct runtime resume rather than a chat turn. UI and chat share the same
resume primitive and a per-thread Postgres advisory lock; submitted UI claims
prevent duplicate answers while queued. Resuming retains the original graph
revision/session and uses a new request ID. Responses follow explicit graph
routes; declining does not implicitly cancel the run. Reload/reconnect restores
pending controls from the checkpoint through the same scoped SSE subscription.

Streaming chat content is transient transport data unless it is persisted into
the underlying conversation state and later surfaced again via canonical
snapshots/detail loads.

## Deployment shape

The default checked-in container stack runs Postgres, frontend, backend, the
Progena runtime, and an internal Claude ACP sidecar. The backend-to-runtime
transport is internal; it must not be confused with the browser's canonical lifecycle
stream or its request-scoped chat response stream.
