# Backend API — Client Contract

This is the authoritative browser-client contract. The browser uses two SSE
transports:

- **Canonical lifecycle/state stream:** `GET /api/ui/stream`
- **Request-scoped response-content stream:**
  `GET /api/ui/responses/{request_id}/stream`

Do not infer an alternate contract from historical response bodies or from an
individual handler's implementation details.

## Transport boundary

`GET /api/ui/stream` establishes the app-lifetime canonical `200 OK`
`text/event-stream` connection. It carries `UiEventEnvelope` frames for
connection lifecycle, retained UI state, and chat response lifecycle.

`GET /api/ui/responses/{request_id}/stream` establishes a request-scoped
`200 OK` `text/event-stream` connection for one accepted streaming chat
response. It carries only response-content frames for that request plus its
transport keepalive/resync signals.

Every retained finite application route—whether it is `GET`, `POST`, `PUT`,
`PATCH`, or `DELETE`—returns only an HTTP `202 Accepted` acknowledgement,
except the active attachment API. Its acknowledgement body is exactly
`{request_id, event_types}`. An acknowledgement MUST NOT contain an entity,
list, query result, status snapshot, generated text, attachment, download, or
an inline response-body SSE stream. When browser chat requests use streaming,
the client opens the dedicated response SSE endpoint separately after the `202`
acknowledgement.

Direct `/health`, `/ready`, `/live`, and `/health/detailed` routes are absent.
`/healthz` is the sole explicit Docker/container liveness exception: it returns
an empty `200 OK`, carries no application data, and is not a browser API route.

## Canonical SSE frames

Each canonical application frame has the SSE event name `ui.event`, an SSE `id`
equal to the envelope `id`, and a JSON `data` value with this exact outer
shape:

```json
{
  "id": "ui-42",
  "seq": 42,
  "type": "chat.sessions.snapshot",
  "scope": "chat",
  "session_id": null,
  "payload": {
    "request_id": "4e58d2dc-88bf-4c10-9bf0-1f2ccce2c100"
  },
  "timestamp": "2026-01-01T00:00:00+00:00"
}
```

| Field | Type | Meaning |
| --- | --- | --- |
| `id` | `string` | Stream-global identifier, currently `ui-{seq}`. It is the deduplication and canonical replay cursor value. |
| `seq` | `integer` | Increasing process-local sequence number. It defines the order of retained canonical replay events. |
| `type` | `string` | Typed domain event, such as `chat.sessions.snapshot` or `logs.page.loaded`. |
| `scope` | `"global" \| "session" \| "chat"` | Event routing scope. It is not an arbitrary nullable string. |
| `session_id` | `string \| null` | Session scope identifier when applicable. |
| `payload` | `object` | Event-specific result data. Every event caused by a finite request also has `payload.request_id`. |
| `timestamp` | `string` | ISO-8601 envelope creation time. |

The SSE event name remains `ui.event` for **all** canonical envelopes,
including connection events. There is no `server.shutdown` SSE event name.

### Canonical connection events

The canonical stream also emits these `ui.event` envelope types:

| Type | Payload | Meaning |
| --- | --- | --- |
| `connection.ready` | `{ "stream": "ui", "status": <status>, "alerts": <alerts> }` | Sent after replay on each connection/reconnection; it marks the canonical live-stream boundary. It is not a request-correlated domain snapshot. |
| `connection.heartbeat` | `{ "timestamp": <unix-seconds> }` | Keepalive frame emitted while the subscriber queue is idle (15 seconds by default, configurable through `SSE_HEARTBEAT_INTERVAL`). |
| `connection.shutdown` | `{ "reason": "server_shutdown", "message": "Server is shutting down" }` | Final canonical envelope before a graceful server-side close. |

Connection events do not resolve a pending finite operation unless their payload
has the matching request ID, which these connection events do not.

## Canonical replay, reconnect, and resync

The backend retains the most recent **200 broadcast application envelopes** in
memory. A reconnect may supply `Last-Event-ID: ui-N`; the server replays
retained envelopes whose `seq` is greater than `N`, in order, then emits
`connection.ready`, then delivers new queued events.

This is bounded best-effort replay, not durable catch-up:

- An absent, malformed, or evicted cursor cannot prove that no event was
  missed. The server does not synthesize an unbounded history or a gap marker.
- `connection.ready` marks the end of whatever replay was available. A client
  that cannot safely continue from its Redux state MUST issue the appropriate
  finite snapshot requests and wait for their correlated stream events.
- Reducers MUST be idempotent for replayed snapshots, updates, and deletes and
  MUST deduplicate by envelope `id`.
- A connection loss does not cancel a finite request. An acknowledgement means
  the causal event was enqueued, not delivered to or applied by this browser.

The app shell owns one canonical `EventSource` for `/api/ui/stream`. There is
no `GET /api/ui/events` stream and no `/api/ui/status` stream.

## Dedicated chat response SSE

Accepted browser chat requests with `stream: true` use a second, request-scoped
transport:

- **Route:** `GET /api/ui/responses/{request_id}/stream`
- **Purpose:** replayable response content for that one request
- **SSE event names:** `chat.response.delta`, `chat.response.event`,
  `chat.response.heartbeat`, `chat.response.resync_required`

This stream does **not** emit `ui.event` envelopes. Its `data` payload is the
response payload directly.

### Replayable dedicated frames

Only these event types are retained and replayed:

- `chat.response.delta`
- `chat.response.event`

Each replayable frame has SSE `id` `chat-response:{request_id}:{seq}`.

Representative payloads:

```json
{
  "id": "response-123",
  "model": "progena-v1",
  "thread_id": "thread_abc",
  "delta": "Hello",
  "request_id": "4e58d2dc-88bf-4c10-9bf0-1f2ccce2c100",
  "runtime_emitted_at_ms": 1730000000123,
  "runtime_stream_seq": 7
}
```

```json
{
  "id": "response-123",
  "model": "progena-v1",
  "thread_id": "thread_abc",
  "event_type": "tool_start",
  "content": "python",
  "metadata": {"tool_name": "python"},
  "images": [],
  "request_id": "4e58d2dc-88bf-4c10-9bf0-1f2ccce2c100",
  "runtime_emitted_at_ms": 1730000000456,
  "runtime_stream_seq": 8
}
```

Structured `chat.response.event` frames preserve the runtime's `metadata` and,
when present, the runtime `images` list. Terminal `usage_update` events keep
aggregate billing usage in `metadata.usage`. During root tool loops, the same
event also carries context-only updates when a complete provider footprint
changes: `metadata.context_usage` is non-aggregate
`{input_tokens, output_tokens, total_tokens, profile_name?, deployment?,
provider_id?, compaction?}`. Persisted chat messages expose the same optional
`context_usage`; missing provider usage is omitted rather than inferred.

`compaction` is an optional `{before_input_tokens, after_input_tokens,
freed_input_tokens, measurement: "comparable_provider_footprints"}` descriptor.
It compares the immediately preceding root provider input footprint with the
first call following a newly committed automatic native summary, only for the
same identified provider/deployment and a positive reduction. This is not exact
tokenizer savings: intervening user/tool/system content may differ. The native
summary itself remains unchanged; a normal after-model state update persists the
descriptor on the AI message and carries it to later root footprints. A newer
summary without a comparable reduction clears the old descriptor. The circle
updates after provider usage arrives, never optimistically resets to zero.

### Native conversation-summary visibility

Summary generation is response content, not a new canonical response lifecycle.
`conversation_summary_started` and `conversation_summary_finished` activity uses
the existing `chat.response.event` lane. A finished summary-model call does not
prove checkpoint persistence; only committed session history may produce a
`Conversation summarized` marker. Request terminal cleanup clears transient
summary activity, including cancellation and failure.

Chat detail and older-message pages carry `summary_markers` for their visible
history window. Marker identity and scope are stable across reload and page
merges. Summary content is not part of chat-list snapshots or token deltas.
The backend resolves native creation boundaries to visible message anchors;
the browser must not interpret checkpoint indices or private SDK state.
Nested-agent summaries belong to their delegated tool activity, not the root
conversation. Original messages remain available and searchable.

Each marker is `{id, anchor_message_id, summary, scope: {kind, id, label?}}`.
Root markers render **after** their anchor message; a null anchor denotes the
transcript head. Scope `kind` is `conversation` (ID is the thread) or `subagent`
(ID is the parent task tool-call ID). Page events include a marker only with its
anchor page, or with the transcript head for a head marker.

Live event metadata is `{summary_id, scope}`. The callback's `summary_id` is not
the durable marker ID. Frontends clear live activity on finish/terminal and
deduplicate committed markers by their history-projected ID. Expanded summaries
are sanitized Markdown; collapsed markers do not parse the summary body.
Marker `summary` is the generated body only. The runtime projection reads the
Progena metadata body or interprets the SDK's native summary delimiters/no-archive
format; browsers never parse prompt-wrapper prose. New model-facing wrappers can
retain retrieval instructions without leaking those instructions into the UI.

Summary-bearing details require messages and summaries from the same checkpoint
revision; an unavailable exact message revision fails the read instead of using
another revision's cached transcript. Child summaries are bounded by the selected
parent checkpoint's timestamp and native ancestry. Independently advancing child
state is not cached under an older root revision. Pinned Postgres message reads
project only the selected message channel and use native DeltaChannel rehydration;
unpinned reads retain `graph.aget_state` pending/live-state semantics. Summary
history uses one request-local metadata index, message writes and distinct native
summary blobs, not full channel hydration for every checkpoint. The backend gets
the complete native message list in one internal vendor projection; it still
publishes the same 50-message browser pages and canonical events. No extra cache
or timeout increase is required.

No additional EventSource, polling, database notification channel, or independent
compaction ledger is used. Summary timestamps describe checkpoint commits, not
exact generation duration; no token-saving percentage is inferred.

### Message output usage and terminal work state

Canonical chat messages carry nullable `token_count` and `final_call_token_count`,
plus `response_status` (`running`, `completed`, `failed`, `cancelled`,
`interrupted`, or `unknown`). The total is explicit provider output summed across
the root-model calls grouped with the visible reply; the final-call count belongs
only to the visible final model response. It is not a count of rendered text,
input tokens, tool-result bytes or delegated-agent usage. Reasoning is included
in output, not added to it. Explicit zero remains zero; missing usage in any
contributing call makes the aggregate unknown. Total-only provider metadata is
not a substitute for output usage.

Reasoning-only checkpoint tails remain visible even without tool calls or answer
text. They retain reported aggregate output but do not invent final-answer usage
or successful completion. Unknown response status does not erase a known final
model call's usage.

The runtime observes provider status and native graph interrupts through the
existing response-content stream; the backend does not create a second ledger
or a notification path. Historical transient provider states remain unknown
without current running evidence. Browser completion callbacks must not turn a
provider-incomplete result into completed work.

Session detail `usage_totals` reports cumulative root assistant output and its
reasoning subset from the full retained checkpoint transcript, before message
paging or display grouping. It contains nullable `output_tokens`,
`reasoning_tokens`, per-metric `output_coverage`/`reasoning_coverage`
(`complete`, `partial`, `unknown`), and `assistant_message_count`,
`output_token_message_count`, `reasoning_token_message_count`. Partial counts
are recorded lower bounds; unknown is not zero. Durable assistant IDs are
counted once. Native compaction preserves the raw transcript, so neither it nor
loading older pages reduces these totals. Explicit history deletion is not a
billing ledger: removed checkpoints/messages cannot be recovered by this view.
The existing snapshot supplies totals without another database/history scan.
Existing session selection/completion reconciliation events update the header;
the browser does not add the live response again on top.



### Dedicated replay and resync semantics

The backend retains the most recent **1024** replayable response-content frames
per request in memory.

Replay cursors are resolved in this order:

1. `Last-Event-ID` header, when supplied
2. `after` query parameter otherwise

Behavior is exact:

- Missing or blank `request_id` → `404 Unknown response stream`
- Unknown `request_id` → `404 Unknown response stream`
- Missing cursor → replay from the start of the retained frame list
- Invalid cursor or a cursor for another request → emit exactly one
  `chat.response.resync_required` frame with
  `{ "request_id": <id>, "reason": "invalid_cursor" }`, then close
- Expired cursor older than the retained buffer → emit exactly one
  `chat.response.resync_required` frame with
  `{ "request_id": <id>, "reason": "cursor_expired" }`, then close
- Valid retained cursor → replay only frames whose retained `seq` is strictly
  greater than the cursor sequence

While the request is still non-terminal and no content frame arrives for
`RESPONSE_STREAM_KEEPALIVE_SECONDS` (currently 15 seconds), the stream emits a
non-replayed transport heartbeat:

```json
{
  "request_id": "4e58d2dc-88bf-4c10-9bf0-1f2ccce2c100",
  "timestamp": 1730000000.123
}
```

as SSE event `chat.response.heartbeat` without an SSE `id`.

The dedicated stream never carries lifecycle ownership. It does **not** emit
`chat.response.started`, `chat.response.completed`, `chat.response.failed`, or
`chat.response.cancelled`. Once the backend knows the request has reached a
terminal lifecycle outcome, the dedicated stream closes after it has replayed or
flushed every queued retained content frame.

## Finite request acknowledgement and correlation

For every finite browser application request, the frontend generates one UUID
and sends it as `X-Operation-Id`. The successful response is exactly:

```json
{
  "request_id": "4e58d2dc-88bf-4c10-9bf0-1f2ccce2c100",
  "event_types": ["chat.sessions.snapshot"]
}
```

An acknowledgement has no optional data field. `request_id` MUST equal the
client header value. The nonempty `event_types` array declares the causal
canonical event type or types that the operation awaits; each declared causal
canonical envelope MUST carry that same value at `payload.request_id`.

The required success ordering is:

1. Validate the request and persist/dispatch its synchronous work.
2. Enqueue every causal canonical `ui.event` envelope, each with
   `payload.request_id`.
3. Return the HTTP `202 Accepted` acknowledgement.

HTTP `202` therefore means **dispatched and accepted**, not browser completion.
The frontend records the acknowledgement as pending and completes the operation
only when an event with both a declared type and the same `request_id` arrives.
An event from another tab or process may update durable Redux data, but MUST NOT
resolve an unrelated local pending operation.

Failures retain a non-`202` HTTP status (for example validation `400`/`422`,
not found `404`, conflict `409`, unavailable `503`, or timeout `504`) and MUST
NOT emit a success event. The client clears a pending operation on HTTP failure
or an explicit UI timeout/reconnect policy; it does not treat the
acknowledgement itself as completion.

For browser chat generation, the acknowledgement and streams divide ownership:

- `POST /api/ui/responses` returns `202 {request_id,event_types}` with
  `event_types: ["chat.response.started"]`
- canonical `/api/ui/stream` carries lifecycle `ui.event` envelopes:
  `chat.response.started`, `chat.response.completed`, `chat.response.failed`,
  `chat.response.cancelled`, and transition-only `chat.response.activity`

`chat.response.activity` is emitted only while a non-terminal response changes
between `generating` and `delegating`. Its payload is exactly:

```json
{
  "request_id": "4e58d2dc-88bf-4c10-9bf0-1f2ccce2c100",
  "thread_id": "thread_abc",
  "activity": "delegating"
}
```

`chat.response.started` establishes the initial `generating` state, so it does
not cause an activity frame. The first live parent `task`, blocking
`a2a_send`/`acp_send`, or explicitly waiting A2A/ACP status call changes
activity to `delegating`; it returns to `generating` only after all concurrent
delegated calls have ended. Background async launches do not enter the
delegating state. Terminal lifecycle frames clear activity; there is no idle
activity frame.

When a thread is selected, its session detail is emitted as both the
request-correlated `chat.session.upserted` and `chat.session.selected` event.
That detail includes `active_request_id` and nullable `activity`
(`"delegating"`, `"generating"`, or `null`) from the same volatile response
registry, not persisted chat history. It also carries nullable `interruption`
(`{next, at}`): when no response is active and the saved transcript ends
mid-turn, the backend reads the latest LangGraph checkpoint directly. It reports
an interruption only for a pending step owned by a non-live runtime, not waiting
on a person and not failed; an unknown live runtime or failed checkpoint read
omits it.

- dedicated `/api/ui/responses/{request_id}/stream` carries only
  `chat.response.delta` and `chat.response.event` content frames, plus
  `chat.response.heartbeat` and `chat.response.resync_required`
- `PUT /api/ui/responses/{generation_id}/cancel` returns
  `202 {request_id,event_types}` with
  `event_types: ["chat.response.cancel_requested"]`
- canonical terminal cancellation still arrives only as
  `chat.response.cancelled` correlated to the **target** response request ID

## Workflow triggers

Workflow graphs require at least one `event`, `cron`, or `manual` trigger.
`manual` triggers never subscribe to document events or the cron clock. Their
object `output_schema` declares the typed payload ports accepted by
`POST /api/ui/workflows/{workflow_id}/trigger` and `run_workflow`; defaults on
payload properties are applied before validation and run creation. An explicit
run chooses the first manual trigger in graph order when one exists, otherwise
the first event or cron trigger remains the compatibility fallback.

## Browser-retained route disposition

The browser-facing retained route families in this table use the acknowledgement
contract above. The listed stream(s) are the result/data source; route
responses never are.

| Domain | Browser routes | Result contract |
| --- | --- | --- |
| Chat sessions | `GET/POST /api/ui/chats`; `POST /api/ui/chats/draft`; `GET/PATCH/DELETE /api/ui/chats/{thread_id}`; `GET /api/ui/chats/{thread_id}/messages` | Canonical `ui.event` envelopes: `chat.sessions.snapshot`, `chat.session.created`, `chat.session.metadata.updated`, `chat.session.upserted`, request-correlated `chat.session.selected` (capped to the newest `CHAT_MESSAGE_PAGE_SIZE` messages, with `has_more_messages`), `chat.messages.page.loaded` (older-page pagination), `chat.session.deleted` |
| Generation | `POST /api/ui/responses`; `GET /api/ui/responses/{request_id}/stream`; `PUT /api/ui/responses/{generation_id}/cancel` | Canonical lifecycle envelopes: `chat.response.started`, transition-only `chat.response.activity`, `chat.response.completed`, `chat.response.failed`, `chat.response.cancelled`; dedicated response frames: `chat.response.delta`, `chat.response.event`; dedicated transport-only frames: `chat.response.heartbeat`, `chat.response.resync_required`; cancel acknowledgement declares `chat.response.cancel_requested` |
| Agent profiles | `GET /api/ui/agent-profiles`; `GET /api/ui/agent-profiles/{agent_id}`; `PATCH /api/ui/agent-profiles/{agent_id}/graph` | Canonical `ui.event` envelopes: `agents.snapshot`, `agent.upserted`, request-correlated `agent.selected`, `agent.graph.updated` |
| Inbox | `GET /api/ui/inbox`; `POST /api/ui/inbox/{message_id}/read`; `POST /api/ui/inbox/{message_id}/decision`; `DELETE /api/ui/inbox/{message_id}` | Canonical `ui.event` envelopes: `inbox.snapshot`, `inbox.message.upserted`, `inbox.message.deleted` |
| Knowledge | `GET/PUT /api/ui/knowledge`; `POST /api/ui/knowledge/{base_id}/sources/{source_id}/index`; `GET /api/ui/knowledge/{base_id}/search` | Canonical `ui.event` envelopes: `knowledge.snapshot`, request-scoped `knowledge.search.results` |
| Workflows | `GET/POST /api/ui/workflows`; `GET/PUT /api/ui/workflows/content`; `DELETE /api/ui/workflows/{workflow_id}`; `POST /api/ui/workflows/{workflow_id}/pause`; `POST /api/ui/workflows/{workflow_id}/resume`; `POST /api/ui/workflows/{workflow_id}/trigger`; `GET /api/ui/workflow-runs`; `GET /api/ui/document-types`; `GET /api/ui/workflows/cron-preview?expression=&timezone=&count=` (synchronous: `{"next_runs": [...]}`, `400` with the validation message) | Canonical `ui.event` envelopes: `workflows.snapshot`, `workflow.updated`, `workflow.deleted`, `workflow.runs.snapshot`, `workflow.run.started`, `workflow.run.updated`, `workflow.run.completed`, `workflow.run.failed`, `document_types.snapshot` (registry with each type's document schema and derived event/read/query port schemas). Runs start from the durable `document_events` log (see `src/backend/events/documents.py`) or from `cron` trigger nodes fired by the backend clock (`src/backend/workflows/cron.py`, last handled due time per node in `backend.state` `workflow_cron:<workflow>:<node>`), never inline from routes. |
| Runs | `GET /api/ui/runs?kind=&status=&q=&period=&sort=&cursor=`; `GET /api/ui/runs/{kind}/{run_id}/trace?client_id=`; `PUT /api/ui/runs/subscription` | Request-correlated `runs.snapshot` (`runs`, opaque `next_cursor`), client-targeted `run.trace.loaded` / `run.trace.updated`, and global `run.updated` / `run.deleted`. `sort` is `newest`, `oldest`, `duration` (longest first), or `shortest`; each opaque cursor is bound to its complete query, including sort, and cannot be reused after a sort/filter change. `RunRow` has `kind`, `run_id`, `name`, `trigger`, `status`, times, nullable recorded `tokens`, and compact `overview` spans. |
| Search | `GET /api/ui/search` | Request-scoped `search.results` (sections: Inbox, Tasks, Agents, Routines, Workflows, Prompts, Knowledge, Tools, Schedules) |
| Knowledge | `GET/PUT /api/ui/knowledge`; `GET /api/ui/knowledge/{base_id}/pages`; `GET /api/ui/knowledge/{base_id}/page`; `GET /api/ui/knowledge/{base_id}/page/revisions`; `GET /api/ui/knowledge/{base_id}/page/revisions/{revision_id}` | Canonical `ui.event` envelopes: `knowledge.snapshot`, `knowledge.pages.snapshot`, `knowledge.page.loaded`, `knowledge.page.revisions.loaded`, `knowledge.page.revision.loaded`; indexer-originated `knowledge.index.started`, `knowledge.index.progress`, `knowledge.index.completed`, `knowledge.index.failed` |
| Workflows | `GET/POST /api/ui/workflows`; `GET/PUT /api/ui/workflows/content`; `DELETE /api/ui/workflows/{workflow_id}`; `POST /api/ui/workflows/{workflow_id}/pause`; `POST /api/ui/workflows/{workflow_id}/resume`; `POST /api/ui/workflows/{workflow_id}/trigger`; `GET /api/ui/workflow-runs` | Canonical `ui.event` envelopes: `workflows.snapshot`, `workflow.updated`, `workflow.deleted`, `workflow.runs.snapshot`, `workflow.run.started`, `workflow.run.updated`, `workflow.run.completed`, `workflow.run.failed` |
| Kanban and tasks | `GET /api/ui/kanban`; `POST /api/ui/kanban/cards`; `PATCH/DELETE /api/ui/kanban/cards/{card_id}`; `GET/PUT /api/ui/tasks/{card_id}/content`; `POST /api/ui/tasks/{card_id}/activity` | Canonical `ui.event` envelopes: `kanban.snapshot`, `task.content.updated`, `task.activity.updated` |
| Widgets and MCP | `GET /api/ui/widgets`; `GET /api/ui/mcp`; `GET /api/ui/mcp/content`; `PATCH/DELETE /api/ui/widgets/{widget_id}`; `POST /api/ui/widgets/{widget_id}/refresh` | Canonical `ui.event` envelopes: `widgets.updated`, `mcp.snapshot` (lean list, no per-tool/log detail), `mcp.server.detail.updated` (full tools/logs for one server) |
| Schedules and event widgets | `GET/POST /api/ui/schedules`; `POST /api/ui/schedules/{schedule_id}/trigger`; `POST /api/ui/events` | Canonical `ui.event` envelopes: `schedule.snapshot`, `widgets.updated` |
| Instructions | `GET /api/ui/instructions`; `GET/PUT /api/ui/instructions/content`; `GET /api/ui/instructions/revisions`; `GET /api/ui/instructions/revisions/{id}`; `POST /api/ui/instructions/revisions/{id}/restore` | `instructions.snapshot` (front matter summaries), `instruction.loaded`, `instruction.revisions.loaded`, `instruction.revision.loaded`. Only `progena/` instructions are editable (403 otherwise); invalid front matter or changed `variables` is a 400. |
| Marketplaces and plugins | `GET/PUT /api/ui/marketplaces`; `GET /api/ui/marketplaces/{name}/plugins`; `GET/POST /api/ui/plugins`; `DELETE /api/ui/plugins/{marketplace}/{plugin}` | Canonical `ui.event` envelopes: `marketplaces.snapshot`, `plugins.snapshot`, request-scoped `plugin.catalog.snapshot`. Skills are no longer authored directly — they ship inside installed marketplace plugins. |
| Alerts | `GET /api/ui/alerts`; `POST /api/ui/alerts/viewed`; `POST /api/ui/alerts/{alert_id}/dismiss`; `POST /api/ui/alerts/{alert_id}/respond` | Canonical `ui.event` envelopes: `alerts.updated` and `alert` for independently generated alerts |
| Logs | `GET /api/ui/logs`; `GET /api/ui/logs/sources`; `GET /api/ui/logs/export` | Canonical `ui.event` envelopes: `logs.page.loaded`, `logs.sources.loaded`, `logs.export.started`, `logs.export.chunk`, `logs.export.completed` |
| Channels | `GET /api/ui/channels`; `PATCH /api/ui/channels/{channel_id}`; `POST /api/ui/channels/{channel_id}/test`; `POST /api/ui/channels/{channel_id}/reconnect` | Canonical `ui.event` envelopes: `channels.snapshot` (each `channel:<id>` document merged with live connection status, per-process message counts, the type's settings `fields` and credential descriptors; never secret values or ciphertext; also published unsolicited when a channel document or connection state changes), request-scoped `channel.tested` (`{channel_id, ok, error?}`). PATCH accepts only `enabled`, a partial `settings` object (unknown or invalid keys are `422`) and `credentials` (`{source:"environment", env_var}`, `{source:"database", value}` encrypted with `PROGENA_PROVIDER_ENCRYPTION_KEY`, or `null` to clear). Reconnect of a turned-off channel is `409`. |

### Runs subscriptions

Runs list snapshots are global, replayable state; a late correlated snapshot
must not overwrite a newer row update. `kind`, `status`, case-insensitive `q`,
and optional `period=hour|day|week|month` filters apply before one global order
across workflow, routine, and schedule runs. `sort=newest` is the default and
orders by descending `(started_at, kind, run_id)`; `sort=oldest` reverses that
order. `sort=duration` orders longest elapsed runs first with the newest
`(started_at, kind, run_id)` tie-breaker; `sort=shortest` orders shortest first
with the oldest tie-breaker. Duration is `completed_at - started_at` or elapsed
through the first page's frozen UTC instant for an unfinished run.

`next_cursor` is opaque and binds that instant plus the normalized `kind`,
`status`, `q`, `period`, and `sort` query. A continuation MUST use the same
filters and sort; a mismatch is `400`. Periods are rolling 1-hour, 24-hour,
7-day and 30-day started-time windows with inclusive UTC lower bounds; omitting
the parameter includes all history. Undated runs are excluded from finite periods.
Workflow cutoff and ordering predicates execute before SQL `LIMIT`. The list
projects compact workflow audit fields and only definition identity/node labels
(not prompt/config bodies), then reads only selected routine checkpoint tips,
including linked workflow routine steps, for overview timing and usage. It does
not hydrate checkpoint histories or send trigger bodies, node outputs, audit
payloads or private checkpoint thread refs.

Trace requests and subscriptions require the caller's active canonical-stream
`client_id`. Targeted trace envelopes are never retained or replayed globally.
`PUT /runs/subscription` accepts `{client_id, kind, run_id}` to select a trace,
or `{client_id}` to clear it. It emits targeted,
request-correlated `run.subscription.updated`; selection also emits a targeted,
request-correlated `run.trace.updated`. Checkpoint-driven updates are targeted
only to current subscribers and carry no request ID. Interests are discarded
when the stream disconnects; clients resubscribe and reload after
`connection.ready`.

`RunTrace` contains spans and hierarchy-safe recorded usage. Known descendant
usage supersedes inclusive parent metadata, including a genuine zero; absent
usage remains null. Full model-message reconstruction can include calls absent
from the list's compact execution metadata. Model
intervals use checkpoint boundaries and are explicitly
`timing_source: "checkpoint-derived"`; they are not provider latency. An open
span has `end: null`. Missing tool audit logs never prove that a tool is open
or failed.

For log export, chunk events carry the download data; the browser reconstructs
the download locally. For browser generation with `stream: true`, lifecycle data
is canonical and response content is request-scoped on the dedicated response
stream. There is no response-body token stream.

Browser generation uses the `/api/ui/responses` routes above. It is distinct
from the backend-to-runtime `/v1/responses` and `/v1/events` protocols, which remain
internal and are not browser APIs.

`POST /api/ui/events` is a finite **event-widget creation** route if present in
the retained widget surface; it is not an EventSource endpoint and returns only
a `202` acknowledgement. Do not confuse it with the deleted `GET /api/ui/events`
legacy stream.

## Removed client surface

The following are intentionally absent rather than compatibility endpoints:

- `GET /health`, `GET /health/detailed`, `GET /ready`, `GET /live`,
  `GET /v1/models`, and `POST /chat`.
- Legacy session, preferences, settings, and conversation APIs. The active
  attachment API remains mounted at `/api/attachments`: `POST` uploads,
  `GET` lists metadata or downloads a thread-scoped blob, and `DELETE` removes
  a thread-scoped blob.
- `POST /agents/register`.
- `GET /api/ui/status/snapshot`, `/loops`, `/agents`, `/backlog`, `/schedule`,
  `/tasks`, `/agents/{agent_name}`, `/database/schema`, and
  `/telegram/status`, plus `POST /api/ui/telegram/test`.
- `GET /api/ui/agent-profiles/graph-schema`, and
  the unmounted workspace-only surface, including `/api/ui/workspace`,
  `/api/ui/widget-schemas`, and their consumers.
- `GET /api/ui/events` and every `/api/ui/status` EventSource variant.

A deleted path returns `404`; it is not a fallback for a stream failure.

## ChatGPT plan provider settings

Provider settings retain the finite acknowledgement/canonical-event contract.
`GET`/`POST /api/ui/providers` and `PATCH`/`DELETE /api/ui/providers/{id}`
publish `providers.snapshot`. Its public payload includes `providers`, `go_models`,
`chatgpt_models` (catalogues keyed by provider ID), and `chatgpt_model_errors`
(safe per-registration errors). A catalogue failure does not hide other providers.

`POST /api/ui/providers/{id}/test` publishes `providers.tested`; ChatGPT tests
consume a supported Responses stream through `response.completed`.
`POST /api/ui/providers/{id}/disconnect` revokes/clears the selected ChatGPT
session and publishes a new `providers.snapshot` after success. Tokens never
appear in these events. Account labels/status are public metadata, not credentials.
ChatGPT model selections are checked against that registration's current catalogue.
Registration and reauthorization use the local CLI and secure server import
described in `docs/DOCKER.md`, not an OAuth callback on a browser API route.

## Insights and estimated model cost

`GET /api/ui/insights/token-usage?range=24h|7d|30d&profile=<optional-name>`
returns a `202` acknowledgement. The correlated
`insights.token_usage.snapshot` event carries `request_id`, range/profile,
UTC start/end, generated time, totals, per-profile/deployment rows, time
buckets, coverage warnings and `pricing_basis: "current_profile_rates"`.
Missing profile means all profiles, including unattributed usage. Totals
include separate input/output/total/cache-read/reasoning counts, call and
incomplete/unpriced counts, and a per-currency array of decimal cost amounts.
Never combine currencies or add cache/reasoning subsets to total tokens.

The backend reads committed `checkpoint_writes` message deltas as well as
message-channel versions in `checkpoint_blobs`. Messages are deduplicated by
thread/message identity, preserving the first observation when no explicit
call/provider timestamp exists. Repeated checkpoint snapshots and workflow
summaries are not additional model calls. Orphan writes without a committed
checkpoint are excluded. Usage retention follows checkpoint retention:
deleting checkpoints may remove reportable history. Missing/unreadable
usage and missing profile identity are reported rather than assigned to
today's deployment catalog.

SQL retrieves the retained serialized message sources in one read; the
LangGraph serializer decodes msgpack and Python aggregates normalized usage.
There is no stored procedure, second authoritative ledger, or hard row cap
on accounting reads. All retained history before the report end is examined
so old messages copied into recent checkpoints are not retimestamped.
This work runs off the async request loop. Measure large installations before
adding a checkpoint-derived projection; current request cost scales with
retained message history, not just the selected range.

Profiles expose optional public/PATCH `pricing` with `currency` and
`input_per_million`, `cached_input_per_million`, `output_per_million` decimal
strings. All rates must be finite/nonnegative; zero is valid; PATCH `null`
removes pricing. Profiles may also carry an optional positive-integer
`context_window`; absent capacity is never inferred from a model name. Cached-input
tokens use the cache rate instead of the input rate. Unknown cache detail is
labelled as an estimate charged at the input rate; unknown pricing is unpriced,
not zero cost. Editing rates recalculates history and does not claim
invoice-grade historical tariffs.
ChatGPT-plan calls retain token counts but do not use profile API prices to
estimate charges; they are reported as unpriced with an explanatory warning.

Profiles also expose optional positive-integer `cache_ttl_seconds` in public
snapshots and PATCH bodies; `null` removes it. The value is a composer countdown
estimate only, never a provider request/retention setting. It is stored beside
`context_window` in the shared `profile` JSONB document and follows the same
validation and canonical `profiles.snapshot` acknowledgement contract. See
[model profile cache estimates](STATE_DOCUMENTS.md#model-profiles) for timing
limitations and provider retention references.

The Profiles editor provides an explicit Azure GPT-5 mini Global Standard
USD preset, verified against the [Azure pricing page](https://azure.microsoft.com/en-us/pricing/details/azure-openai/)
on 2026-10-20: input `0.25`, cached input `0.025`, output `2.00` per million
tokens. Displayed rounded prices are not used for calculation. This preset
does not establish prices for other models, regions or deployment tiers.
An absent catalog is seeded with this pricing only when
`PROGENA_MODEL_DEFAULT_MODEL=gpt-5-mini` and
`PROGENA_MODEL_DEFAULT_PRICING_TIER=global_standard` are both explicit.
Existing catalogs are never silently repriced; arbitrary deployment names
remain unpriced until configured.


## Local development

Start the runtime and backend separately:

```bash
source .venv/bin/activate
export AGENT_NAME=progena
python -m src.progena.main
```

```bash
source .venv/bin/activate
python -m uvicorn src.backend.main:app --host 0.0.0.0 --port 8000
```

The frontend development server proxies `/api`, `/v1`, and operational health
requests to the backend. Client code should use this document—not proxy details
or historical endpoint responses—as its API contract.
