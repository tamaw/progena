# Event-Stream-Only API Refactor Plan

## Decision

This is a clean cutover. The frontend is the sole API consumer.

After this refactor, the browser receives application data only through the canonical `GET /api/ui/stream` Server-Sent Events connection. HTTP requests submit work; they never carry application state in their success bodies.

There are no compatibility routes, response-body fallbacks, aliases, migrations, or deprecated client paths.

### Explicit operational exception

`GET /healthz` is the sole exception to the event-stream-only API rule. Docker
and container supervisors use it only as a dependency-free process liveness
probe. It returns an empty `200 OK`, carries no application data, and is not a
browser API route. Every browser application-data request remains governed by
the canonical `/api/ui/stream` contract.

## Target contract

### One transport for application data

`GET /api/ui/stream` remains the single long-lived browser data transport. It continues to return `200 text/event-stream`: this is a stream establishment, not an accepted work request.

Every other retained application request returns **`201 Created`** after the backend has accepted and completed the request's synchronous persistence/dispatch work and has enqueued its causal `ui.event` envelope(s). This includes retained `GET`, `POST`, `PUT`, `PATCH`, and `DELETE` requests.

A successful request has this sole response shape:

```json
{
  "accepted": true,
  "request_id": "<client-generated operation id>",
  "event_types": ["<causal ui.event type>"]
}
```

Implement `UiRequestAccepted` once as a Pydantic model and one helper for constructing it. It must be the only successful finite application-response model.

Rules:

1. The frontend generates one UUID operation ID for every application request and sends it in `X-Request-ID`. Existing detail routes may retain a query `request_id` only until all callers use the header; the value must be identical.
2. The response returns that exact operation ID. It has no entity, list, status, query result, error, or generated-text field.
3. Every causal `ui.event.payload` includes `request_id`. A snapshot caused by a request includes it too.
4. Persist first; enqueue the event only after persistence succeeds; return `201` only after enqueueing the event(s).
5. `201` means **accepted and enqueued**, not delivered to or applied by a browser. The frontend keeps the operation pending until its matching event arrives, the request fails, or an explicit timeout/reconnect policy ends it.
6. Validation, missing-resource, conflict, and internal failures keep their existing non-201 status semantics and emit no success event.
7. Events from another tab/process update Redux state but do not resolve this tab's unrelated pending operation.
8. Reducers must be idempotent for replayed snapshots, updates, and deletes. Existing `UiEventEnvelope` identifiers and replay remain the dedupe mechanism.

The existing envelope remains the outer transport shape:

```json
{
  "id": "ui-…",
  "seq": 0,
  "type": "domain.action",
  "scope": "…",
  "session_id": "…",
  "payload": { "request_id": "…" },
  "timestamp": "…"
}
```

Source: `app/backend/chat_sessions.py:92-99`; producer/replay transport: `app/backend/ui_routes.py:5146-5273`; browser listener: `app/frontend/src/store/realtimeListener.js:28-72`.

## Route disposition

### Retain and convert to `201` acknowledgement plus `ui.event`

All retained routes below return `UiRequestAccepted`; the listed event is the only source of result data.

| Domain | Routes | Required causal events |
|---|---|---|
| Chat sessions | `GET/POST /api/ui/chats`; `POST /api/ui/chats/draft`; `GET/PATCH/DELETE /api/ui/chats/{thread_id}` | `chat.sessions.snapshot`, `chat.session.created`, `chat.session.metadata.updated`, `chat.session.upserted`, request-correlated `chat.session.selected`, `chat.session.deleted` |
| Agent profiles | `GET /api/ui/agent-profiles`; `GET /api/ui/agent-profiles/{agent_id}`; `PATCH /api/ui/agent-profiles/{agent_id}/graph` | `agents.snapshot`, `agent.upserted`, request-correlated `agent.selected`, `agent.graph.updated` |
| Inbox | `GET /api/ui/inbox`; `POST /api/ui/inbox/{message_id}/read`; `DELETE /api/ui/inbox/{message_id}` | `inbox.snapshot`, `inbox.message.upserted`, `inbox.message.deleted` |
| Knowledge | `GET/PUT /api/ui/knowledge`; `POST /api/ui/knowledge/{base_id}/sources/{source_id}/index`; `GET /api/ui/knowledge/{base_id}/search` | `knowledge.snapshot`; add request-scoped `knowledge.search.results` for search results |
| Workflows | workflows list/content/create/delete/pause/resume, workflow-runs list, and trigger routes | `workflows.snapshot`, `workflow.updated`, `workflow.deleted`, `workflow.runs.snapshot`, `workflow.run.started|updated|completed|failed` |
| Kanban/tasks | board/card/task-content/task-activity routes | `kanban.snapshot`, `task.content.updated`, `task.activity.updated` |
| Widgets/MCP/schedules/event widgets | widget/MCP/schedule/event-widget routes, including refresh and trigger | `widgets.updated`, `mcp.snapshot`, `schedule.snapshot`; add causal refresh/trigger events where absent |
| Prompts and skills | retained catalogue/write/create routes | `prompts.snapshot`, `skills.snapshot` |
| Alerts and UI status | alert read/mutation routes; any retained status request | `alerts.updated`, `alert`, `status.updated`, `connection.ready` |
| Log browsing/export | retained logs list/filter/source/export operations | add request-scoped `logs.page.loaded`, `logs.sources.loaded`, and chunked `logs.export.started|chunk|completed` events; the frontend builds downloads locally |
| Generation | `POST /v1/responses`; `PUT /v1/responses/{request_id}/cancel` | add request-correlated `chat.response.started|delta|completed|failed|cancelled`; move token consumption from response-body SSE to canonical `ui.event` frames |
| Session / health, only if retained during cutover | existing frontend-used session/health requests | emit request-correlated `session.updated` / `status.updated`; otherwise replace their frontend behavior and delete their routes |

No retained finite endpoint may return resource JSON, a `204`, a file attachment, a raw response stream, or a direct query result.

### Delete rather than convert

Delete these endpoints because the frontend has no live callsite or their consumer is being removed. Remove route-specific schemas, helpers, API config, tests, docs, imports, and dead frontend code in the same change.

- `GET /health/detailed`, `GET /ready`, `GET /live`, `GET /v1/models`, `POST /chat`.
- Attachment upload/download endpoints.
- Legacy session/preferences/settings/conversation endpoints after the `/v1/responses` client no longer needs them.
- `POST /agents/register`; retain registry read/watch machinery used by profiles.
- `GET /api/ui/status/snapshot`, `/loops`, `/agents`, `/backlog`, `/schedule`, `/tasks`, `/agents/{agent_name}`, `/database/schema`, `/telegram/status`, and `POST /api/ui/telegram/test`.
- `GET /api/ui/tasks/{card_id}/content`, `GET /api/ui/prompts/content`, `GET /api/ui/skills/content`, and the route-only `PromptService.get_prompt` / `SkillService.get_skill` helpers.
- `GET /api/ui/agent-profiles/graph-schema` unless a live page is introduced during this refactor.
- The unmounted Workspace surface and its response-only API client: `WorkspaceContext`, workspace graph/components/nodes, `useWorkspaceApi`, `usePromptsApi`, `useSkillsApi`, `PromptLibraryPanel`, and route configuration only consumed by them. Keep `WorkflowJsonPanel`, which `WorkflowsPage` imports.
- The nonexistent frontend `/api/ui/status` and `/api/ui/events` EventSource configuration, hook, documentation, and consumers. No backend route exists for either path.

The deletion list is source-grounded by `app/frontend/src/context/ConfigContext.jsx`, `hooks/useSSE.js`, `context/ChromeRuntimeContext.jsx`, `context/WorkspaceContext.jsx`, `store/uiData/*`, and the corresponding backend route handlers. Before deleting a shared helper, use LSP references and retain any implementation still used by stream watchers or retained routes.

## Implementation phases

### 1. Establish the contract and stream primitives

**Backend**

1. Add `UiRequestAccepted` and an acknowledgement builder in the backend response/state model layer.
2. Make request correlation explicit in the event API: every `broadcast_ui_event()` call caused by a request accepts and embeds `request_id`.
3. Preserve `Last-Event-ID` replay and `connection.ready`. Extend it only where a retained page needs a reliable bootstrap snapshot.
4. Fix event payload gaps before any client stops reading HTTP bodies:
   - add `request_id` to all snapshot/update/delete/run events;
   - add `alert` handling in Redux or replace it with an `alerts.updated` event;
   - emit `schedule.snapshot` for an accepted schedules GET;
   - emit `mcp.snapshot` immediately after MCP mutations;
   - normalize widget event payload serialization;
   - define `workspace.snapshot` and `widget-schemas.snapshot` only if their currently dead workspace UI is retained; otherwise delete both surfaces;
   - define query-result and export-chunk events for retained knowledge/log features;
   - define chat response delta/completion events for `/v1/responses`.
5. Keep event generation after the write succeeds and before the `201` acknowledgement. Do not suppress the existing always-on kanban workflow-reconciliation watcher; it has runtime behavior independent of browser connections.

**Frontend**

1. Make `realtimeListener.js` and `RealtimeBridge.jsx` the only owners of application realtime state.
2. Add a pending-operation registry to `realtimeSlice` keyed by `request_id`.
3. Add a request helper distinct from `requestJson` / generic response readers. It validates exactly `201`, parses only `UiRequestAccepted`, records pending event types, and never installs returned domain data.
4. Preserve a separate low-level helper only for opening the canonical EventSource. After this phase there is no second EventSource hook.

### 2. Convert Redux domains to event-only hydration

For every retained `uiData` thunk:

- `pending`: record request metadata only.
- fulfilled `201`: record acknowledgement only; do not clear event-confirmation loading and do not mutate entity/list/detail data.
- rejected: clear pending and set an error; no success event must be applied.
- matching `ui.event`: update the domain reducer, clear the correlated pending operation, then let pages select the new state.

Convert these modules first because their reducers already understand most event types:

1. `app/frontend/src/store/uiData/chats.js`
2. `app/frontend/src/store/uiData/agents.js`
3. `app/frontend/src/store/uiData/inbox.js`
4. `app/frontend/src/store/uiData/tasks.js`
5. `app/frontend/src/store/uiData/knowledge.js`
6. `app/frontend/src/store/uiData/workflows.js`
7. `app/frontend/src/store/uiData/prompts.js`
8. `app/frontend/src/store/uiData/skills.js`
9. `app/frontend/src/store/uiData/runtime.js`
10. `app/frontend/src/store/uiDataSlice.js`

Remove direct-body extra reducers, direct response upserts, direct delete removals, inbox's HTTP snapshot fallback, and response-derived selection/navigation. Keep only carefully specified reversible UI affordances; durable state remains event-owned.

### 3. Move live pages and components to Redux selectors

Update live consumers so they dispatch a request and wait for selector changes caused by the matching event:

- `ConversationPage.jsx`, `useChatSessionPrefs.js`, `useChatStream.js`: no direct chat body, session body, or response-body token parser. Create/select drafts from correlated chat events. Consume `chat.response.*` from Redux.
- `AgentsPage.jsx`, `InboxPage.jsx`, `TasksPage.jsx`, `SchedulesPage.jsx`, `ToolsPage.jsx`, `KnowledgePage.jsx`, `PromptsPage.jsx`, `SkillsPage.jsx`, `WorkflowsPage.jsx`: remove `unwrap()` body usage and direct body-derived selection/draft logic.
- `LogsPage.jsx`: retain the user-visible log/search/export capability but read result/chunk state from Redux events, not a response body.
- Replace `AlertCenterContext` with Redux selectors and acknowledgement thunks. Keep purely local view state in the panel.
- Delete `ChromeRuntimeContext` and `useSSE`; consume `selectUiStatus`, `selectUiWidgets`, `selectUiAlerts`, and realtime connection state directly.
- Remove the legacy `/api/ui/status` streams. `LogsPage` reacts to canonical event metadata or a typed `logs.updated` event, not a private EventSource.
- Replace `HealthContext` polling with canonical stream connection/status state, then delete `/health` if it has no remaining application caller.

### 4. Convert backend route families

Convert in this dependency order. Each family is complete only when the backend returns 201-only acknowledgements, the needed event contains the complete result, Redux hydrates from that event, and no page reads the former body.

1. Chats, agent profiles, and inbox — establish request correlation using the existing detail-selection precedent.
2. Knowledge — include `knowledge.search.results` for request-scoped search.
3. Workflows and workflow runs — include trigger/run correlation and remove response-derived workflow drafts.
4. Kanban, task activity, and retained task content.
5. Widgets, MCP, schedules, event widgets, and alerts.
6. Prompts and skills.
7. Logs/export and foreground generation — replace direct data streams with event chunks while preserving cancellation and error states.
8. Health/session only if still needed after earlier deletions; otherwise delete rather than create a redundant event contract.

For every converted handler update:

- decorator `status_code=201` and `response_model=UiRequestAccepted`;
- all successful deletes return 201, never 204;
- success path returns only the acknowledgement helper;
- event payload supplies every field formerly required by the frontend;
- failure behavior remains explicit and non-201;
- no endpoint calls another route handler merely to reuse an old body response; extract a state operation/helper instead.

### 5. Delete obsolete surface area

After the event-only client cutover, delete every unreferenced route listed above and all code made unreachable by it. Use LSP references before each exported-symbol deletion.

Required removals include:

- dead endpoint keys (`events`, `status`, `healthDetailed`, `chat`, `models`) from `ConfigContext` once their dependent surface is gone;
- `useStatusSSE`, `ChromeRuntimeContext`, and `AlertCenterContext`;
- stale workspace provider, graph, API hooks, unmounted workspace components, and their tests;
- request thunks with no callsite, such as `fetchTaskContent`;
- route-only prompt/skill getter methods and their models/tests;
- old direct-response models only after their event payload schemas no longer need them.

Do not remove `StateReader`, document-store watchers, the registry state, or shared serializers merely because a direct route was deleted. They remain needed by canonical stream broadcasts and retained routes.

### 6. Rewrite documentation and tests last

Make `docs/BACKEND.md` the one authoritative client contract. It must define:

- the sole `/api/ui/stream` transport;
- envelope schema, replay, ordering, and reconnect behavior;
- the exact 201 acknowledgement schema and correlation rule;
- persistence/event/ack ordering;
- error and timeout semantics;
- the rule that data does not arrive in successful HTTP responses;
- the absence of legacy status/events streams and direct response fallbacks.

Update `docs/ARCHITECTURE_DIAGRAM.md`, `docs/STATE_DOCUMENTS.md`, `docs/SPEC.md`, `docs/CHAT_FRONTEND_NOTES.md`, `docs/CHAT_PAGE_TEST_PLAN.md`, and frontend README references. Remove stale `/api/ui/events`, `/api/ui/status`, and old `200` request-then-stream claims.

## Test plan and acceptance criteria

Use focused tests, then targeted integration flows. Do not retain tests that assert the superseded 200/204/body contract.

### Backend contract tests

1. Table-drive every retained finite route: valid request returns exactly `201` and `UiRequestAccepted`; its body contains no application data.
2. For every request, capture `/api/ui/stream` and prove the causal event includes the same `request_id`, expected type, and enough data for Redux hydration.
3. Assert persistence happens before event/ack; rejected operations emit no success event.
4. Assert stream headers, `ui.event` frame name, envelope shape, heartbeat, `Last-Event-ID` replay filtering, ordering, and reconnect bootstrap.
5. Assert concurrent requests of the same type resolve only their matching pending operation, including reversed event arrival.
6. Assert delete operations are 201 acknowledgements followed by event-based Redux removal; replayed deletes are safe.
7. Assert direct data paths are absent from OpenAPI except `GET /api/ui/stream`; assert deleted endpoint paths return 404.
8. Cover chunked generation and log-export events, cancellation, failure, and client reconstruction of output/download data.

Primary backend test rewrite targets: `tests/backend/test_ui_routes.py`, `test_workflow_routes.py`, `test_inbox_routes.py`, `test_agent_profile_routes.py`, `test_async_responses.py`, `test_chat_events.py`, `test_ui_feedback.py`, `test_e2e_ui_feedback.py`, `test_first_contact_critical.py`, and `test_kanban_trigger_reconciliation.py`.

### Frontend contract tests

1. Mock one EventSource and prove `RealtimeBridge` opens only `/api/ui/stream`; no code opens `/api/ui/status` or `/api/ui/events`.
2. For every domain, prove a 201 acknowledgement does not hydrate data; only a matching event does.
3. Prove duplicate/replayed envelopes are idempotent, malformed frames report realtime error, and reconnect replay preserves pending-operation correlation.
4. Prove no stale/out-of-order detail, create, update, or delete event resolves a newer request.
5. Prove page interactions remain pending after 201 and complete only after the corresponding selector changes.
6. Prove foreground generation and log export reconstruct from canonical event chunks.

Primary frontend test targets: `app/frontend/src/store/realtimeSlice.unit.test.js`, `uiDataSlice.unit.test.js`, new listener/bridge tests, and affected page/model tests.

### Final proof

The refactor is complete only when:

- Exactly one browser EventSource is constructed in the application.
- It targets only `/api/ui/stream`.
- Every retained finite application request returns exactly `201 UiRequestAccepted` on success.
- No successful finite response body contains application data.
- Every accepted request has a matching causal event with the same `request_id`.
- Redux is the sole durable frontend application-data owner.
- All static-unused routes and their route-only code are absent.
- No fallback, compatibility route, direct-response reducer, duplicate context, or deprecated documentation remains.
