# Event-Stream-Only Refactor Audit

**Audited:** 2026-07-12
**Scope:** Current implementation against `EVENT_STREAM_REFACTOR_PLAN.md`
**Result:** **1 / 5 — not ready to merge or deploy**

## Executive result

The implementation has a credible structural start—`UiRequestAccepted`, many converted route decorators, a single canonical frontend `EventSource`, and several event reducers—but it does not satisfy the event-stream-only contract end to end.

The acknowledgement-to-pending-operation-to-causal-event lifecycle is disconnected. Multiple mounted frontend flows still consume finite HTTP response bodies or raw response-body streaming. Several retained backend routes either crash, advertise events they do not emit, or still return direct application data. Frontend linting fails with syntax and undefined-symbol errors.

## Confirmed conforming behavior

- One `new EventSource` construction was found: `app/frontend/src/store/realtimeListener.js:39`.
- The configured canonical stream is `/api/ui/stream`: `app/frontend/src/context/ConfigContext.jsx:31-36`.
- `RealtimeBridge` owns stream startup/shutdown: `app/frontend/src/store/RealtimeBridge.jsx:18-23`.
- No frontend `/api/ui/status` or `/api/ui/events` EventSource usage was found.
- Backend canonical stream still provides `ui.event` frames, `Last-Event-ID` replay, `connection.ready`, and heartbeats: `app/backend/ui_routes.py:4731-4809`.
- `UiRequestAccepted` and `accepted_response()` exist: `app/backend/chat_sessions.py:102-115`.
- Chat, agent-profile, inbox, knowledge, and many UI route handlers now declare `201` and `UiRequestAccepted`.
- Chat and agent detail reducers reject a mismatched `request_id` selection event.
- Realtime envelope-ID dedupe remains present and is covered by the existing reconnect unit test.

## Critical findings

### 1. `requestAccepted201` does not enforce exact `201 UiRequestAccepted`

**Evidence:** `app/frontend/src/store/http.js:91-136`

The helper accepts every `response.ok` status, including 200, 202, and 204. It fabricates a successful acknowledgement for empty or malformed bodies and returns arbitrary parsed JSON without verifying:

- `response.status === 201`;
- `accepted === true`;
- response `request_id` equals sent `X-Request-ID`;
- `event_types` is a string array.

**Impact:** legacy bodies, 204 responses, malformed proxy responses, or acknowledgements for another operation are all treated as success.

**Required correction:** reject all statuses except 201; parse one JSON acknowledgement; validate the exact contract; never synthesize an acknowledgement.

### 2. Pending-operation correlation is dead state and resolves incorrectly

**Evidence:**

- `app/frontend/src/store/realtimeSlice.js:56-82`
- `app/frontend/src/store/realtimeListener.js:58-59`

`pendingOps` and its reducers exist, but static source search found no call site for `registerPendingOp`, `resolvePendingOp`, or `failPendingOp` outside `realtimeSlice.js`.

If it were registered, `uiEventReceived` deletes a pending entry for any matching `payload.request_id`, without checking the event type against stored `eventTypes`. It also resolves before `applyUiEvent` updates the domain reducer.

**Impact:** the defining HTTP acknowledgement → pending operation → matching event lifecycle does not work.

**Required correction:** register a validated acknowledgement after HTTP 201; apply each domain event first; resolve only when both `request_id` and expected event type match; fail/clear operations on rejected requests and explicit timeout/reconnect policy.

### 3. Mounted pages and hooks still consume finite response data

**Evidence:**

- Generic direct-body client: `app/frontend/src/hooks/api/useApiClient.js:21-76`
- Tasks API: `app/frontend/src/hooks/api/useTasksApi.js:8-27`
- Schedules API: `app/frontend/src/hooks/api/useSchedulesApi.js:8-23`
- Widgets API: `app/frontend/src/hooks/api/useWidgetsApi.js:8-28`
- Logs API: `app/frontend/src/hooks/api/useLogsApi.js:8-17`
- Tasks page uses returned activity as durable state: `app/frontend/src/pages/TasksPage.jsx:684-694`
- Schedules page reads `data.items` and dispatches snapshot state from it: `app/frontend/src/pages/SchedulesPage.jsx:300-331`
- Logs page hydrates entries, selections, groups, and cursors from HTTP response fields: `app/frontend/src/pages/LogsPage.jsx:435-505`

`useApiClient` neither sends `X-Request-ID` nor enforces 201, then reads and returns finite success bodies.

**Impact:** these live flows break against acknowledgement-only endpoints and retain forbidden HTTP-owned application state.

### 4. Chat still parses a second raw response-body SSE stream

**Evidence:**

- `app/frontend/src/hooks/api/useChatApi.js:80-181`
- `app/frontend/src/hooks/useChatStream.js`
- `app/frontend/src/pages/ConversationPage.jsx`
- `app/frontend/src/components/layout/AppRuntimeShell.jsx`

`useChatApi.sendMessageStream()` POSTs `/v1/responses`, accepts generic `response.ok`, reads `response.body.getReader()`, parses raw chunks, and supplies deltas/completion through callbacks.

Cancellation at `useChatApi.js:186-206` sends no `X-Request-ID` and does not validate 201. Session calls at `useChatApi.js:76-78` target session routes removed from the backend.

**Impact:** mounted chat behavior remains dependent on an application-data transport other than `/api/ui/stream`.

### 5. Alert mutations crash at runtime

**Evidence:** `app/backend/ui_routes.py:3907-3945`

`dismiss_alert()` and `respond_to_alert()` declare `x_request_id` but call:

```python
broadcast_alerts_state(request_id=request_id)
```

without assigning `request_id`.

**Impact:** existing-alert dismiss/respond paths raise `NameError` rather than broadcast `alerts.updated` and return 201.

### 6. Logs sources and exports remain direct HTTP data paths

**Evidence:** `app/backend/ui_routes.py:4147-4251`

- `/api/ui/logs/sources` directly returns JSON and has no acknowledgement/event contract.
- `/api/ui/logs/export` emits only `logs.export.started`, then returns file attachment responses.
- Explicit `Response(...)` branches make its effective success status 200, not the decorator’s 201.
- No `logs.sources.loaded`, export chunks, or export completion event exists.

**Impact:** logs violate the explicit no-direct-body rule, and browser-side export reconstruction is absent.

### 7. Workflow trigger advertises an event it does not causally emit

**Evidence:**

- `app/backend/ui_routes.py:2819-2854`
- `app/backend/workflow_service.py:399-430`
- `app/backend/workflow_service.py:545-546`

The trigger route returns:

```python
accepted_response(request_id, ["workflow.run.started"])
```

but does not synchronously emit a correlated `workflow.run.started`, nor pass the request ID into later executor events.

**Impact:** a client awaiting the acknowledged causal event can remain pending indefinitely.

### 8. Refactored `action_run` is still used as an old response-body helper

**Evidence:**

- `app/backend/ui_routes.py:646-679`
- `app/backend/ui_routes.py:3732-3796`
- `app/backend/chat_sessions.py:102-115`

Workflow prompt execution accesses `response.response` and `response.thread_id` from `action_run()`, but that route now returns `UiRequestAccepted` containing only `accepted`, `request_id`, and `event_types`.

**Impact:** workflow prompt execution raises `AttributeError`.

**Required correction:** extract the underlying domain operation; do not call a converted route handler to recover old response data.

### 9. Generation contract diverges from the plan

**Evidence:** `app/backend/main.py:1143-1264`

- Errors emit `chat.response.error`, but the contract specifies `chat.response.failed`.
- The response acknowledgement advertises only started/delta/completed and not terminal failed/cancelled alternatives.
- Cancellation lacks an operation-level `X-Request-ID`; it reuses the target generation ID as its acknowledgement/event ID and can emit duplicates.

### 10. Additional missing or inconsistent causal events

- Inbox mutation upsert/delete events omit `request_id`; only later snapshots are correlated:
  - `app/backend/inbox_routes.py:144-147`
  - `app/backend/inbox_routes.py:157-161`
- Filtered prompts/skills GETs advertise snapshots but do not emit an event when `agent` filtering is used:
  - `app/backend/ui_routes.py:3952-3974`
  - `app/backend/ui_routes.py:3999-4018`
- Action routes acknowledge event types they never emit:
  - `app/backend/ui_routes.py:3783-3796`
  - `app/backend/ui_routes.py:3842-3854`
- MCP mutations update widgets but do not publish required `mcp.snapshot`.
- `/health` remains a direct JSON finite endpoint: `app/backend/main.py:971-986`.

## High and medium frontend defects

### Request IDs are not guaranteed UUIDs

**Evidence:**

- `app/frontend/src/pages/ConversationPage.jsx:310-315,366-370`
- `app/frontend/src/pages/AgentsPage.jsx:469-471`
- `app/frontend/src/hooks/api/useChatApi.js:92`
- `app/frontend/src/store/uiData/chats.js:140-146`

Several call sites use human/random strings or prefixed IDs rather than UUID values. Other thunks reuse Redux Toolkit IDs without an explicit UUID generator or validator.

### UI treats acknowledgement settlement as completion

Many reducers clear loading on thunk fulfillment before a causal event updates state, including chats, agents, inbox, workflows, knowledge, tasks, MCP, prompts, and skills.

Pages also immediately transition selection, navigation, or success state after dispatch settlement. This violates the requirement to stay pending until matching event-driven state changes.

### Inbox optimistic durable updates have no rollback

**Evidence:** `app/frontend/src/store/uiData/inbox.js:90-120`

Read/delete apply durable changes during pending with no rejected-case rollback. A failed operation can leave the local inbox stale until an unrelated snapshot arrives.

### Rejected thunk actions are treated as success

Several handlers `await dispatch(...)` without `.unwrap()`, then execute success transitions because Redux Toolkit resolves dispatch with a rejected action rather than throwing.

Examples:

- `app/frontend/src/pages/PromptsPage.jsx:263-267`
- `app/frontend/src/pages/SkillsPage.jsx:242-278`
- `app/frontend/src/pages/WorkflowsPage.jsx:1462-1522`
- `app/frontend/src/pages/ConversationPage.jsx:1052-1098`

### Plan-designated response-era workspace surface remains

The unmounted workspace artifacts were only partially removed. `workspaceGraph.js`, workspace node components, `PromptLibraryPanel`, and response-reading prompt/skill hooks remain. Workspace node files now reference deleted `useWorkspaceActions`/`useWorkspaceMeta` symbols.

## Verification results

### Backend tests: not runnable in this checkout

Attempted command:

```bash
source .venv/bin/activate && pytest \
  tests/backend/test_ui_routes.py \
  tests/backend/test_workflow_routes.py \
  tests/backend/test_inbox_routes.py \
  tests/backend/test_agent_profile_routes.py \
  tests/backend/test_async_responses.py \
  tests/backend/test_chat_events.py -q
```

Result: failed immediately because this checkout has no `.venv/bin/activate`.

No sibling project virtual environment was used.

### Frontend build: blocked by missing installed dependency

`npm run build` failed before source compilation because Rollup could not resolve declared dependency `react-redux` from `src/main.jsx`.

This establishes an environment/dependency-installation issue, not by itself a refactor source defect.

### Frontend lint: failed with 20 errors

Confirmed refactor-relevant source failures include:

1. **Unclosed JSX root**
   - `app/frontend/src/components/layout/AppRuntimeShell.jsx:1584-1616`
   - `<div className={shellClass}>` has no closing `</div>`.

2. **Malformed prompts reducer module**
   - `app/frontend/src/store/uiData/prompts.js:58-73`
   - `buildExtraReducers()` has a dangling reducer chain before exported selectors.

3. **Deleted workspace provider left undefined symbols**
   - `app/frontend/src/components/workspace/nodes/AgentNode.jsx:114-115`
   - Similar undefined `useWorkspaceActions`/`useWorkspaceMeta` errors in CodeNode, EventNode, GroupNode, LabelNode, PromptNode, ScheduleNode, UserNode, and WebFrameNode.

4. **Cleanup defects**
   - `app/frontend/src/store/uiData/chats.js:70`: unused `moveChatToFront`.
   - `app/frontend/src/hooks/api/useApiClient.js:36`: unused callback parameter.

## Test coverage assessment

Current tests do not prove the new contract. Some still assert the superseded direct-body behavior:

- `tests/backend/test_ui_routes.py:177-214`: resource bodies / 200.
- `tests/backend/test_inbox_routes.py:81-144`: inbox body / 204 delete.
- `tests/backend/test_workflow_routes.py:74-107`: workflow bodies / 204 delete.
- `tests/backend/test_async_responses.py:175-201`: old cancellation body.
- `app/frontend/src/store/uiDataSlice.unit.test.js:81-163`: direct fulfilled chat hydration.
- `app/frontend/src/store/uiDataSlice.unit.test.js:509-515`: fulfilled inbox response hydration.

Missing high-value tests:

1. Parameterized retained-route 201 acknowledgement contract checks.
2. `X-Request-ID` → response ID → canonical stream event ID round trip.
3. Persistence-before-event-before-ack ordering.
4. No success event after rejected persistence.
5. Pending operation registration, event type matching, rejection, timeout, and reconnect behavior.
6. Reversed arrivals for concurrent same-type operations.
7. Exactly one `/api/ui/stream` EventSource test.
8. Event-only Redux hydration assertions.
9. Generation delta/completion/failure/cancellation reconstruction.
10. Log page/source/export chunk reconstruction and browser download generation.
11. Deleted endpoint 404/OpenAPI absence checks.

## Recommended recovery order

1. Restore a buildable frontend: fix `AppRuntimeShell.jsx`, `prompts.js`, and remove the entire unmounted workspace surface or restore the removed dependencies temporarily.
2. Make `requestAccepted201` strict and wire the pending-operation lifecycle correctly.
3. Remove all remaining direct finite-response readers and raw `/v1/responses` body parsing.
4. Fix backend runtime blockers: alert `NameError`, direct logs responses, workflow trigger correlation, action-run coupling, cancellation correlation, event-name mismatches, and absent MCP snapshots.
5. Rewrite stale tests and add end-to-end request → canonical stream → Redux correlation coverage.
6. Create/install the checkout-local Python venv and frontend dependencies, then rerun focused tests, lint, and build.

## Conclusion

This is a partial mechanical conversion, not a complete event-stream-only cutover. The current implementation is structurally promising but operationally incomplete and contains confirmed runtime, contract, and syntax defects.
