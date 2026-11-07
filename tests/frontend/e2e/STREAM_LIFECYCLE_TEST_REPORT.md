# PR #492 test report: possible problems

Stack: isolated (Postgres, Progena runtime, backend `:28000`, built frontend `:24173`) holding a copy of
cronix data (157 chats, 126 routines, 20 workflows, 199 workflow runs, 63 alerts). No working model provider.
Baseline for comparison: `origin/main` built and served against the same database (backend `:28100`,
frontend `:24174`), since removed. Everything below was observed; `[INFERENCE]` marks the exceptions.

## Summary

| Area | Result |
|---|---|
| Wire data vs main | Much better. Idle tab: 0.2 KB vs 5.2 MB during another tab's tour. Cold page loads about 2x faster. |
| Backend restart, reconnect | Works. Each resource requested once, topics registered before snapshots. |
| Cross-tab provider rename | Works. The other tab got `providers.snapshot`. |
| Full e2e suite | 49 passed, 43 failed, 16 skipped, 2 not run. 36 of the failures also fail on main. 7 do not (see P1). |
| Page tour (22 routes) | No console errors, no uncaught errors, no 4xx/5xx on the 21 routes that exist. |

## P1. Regressions in the existing e2e suite caused by this PR (stale specs)

The PR makes `client_id` required on about 70 finite `/api/ui/*` routes, with a 409 when that client has no
open stream. On main, for example, `PUT /api/ui/workflows/content` and `GET /api/ui/agent-profiles` took none.
The page now sends `…/run?client_id=…`, so specs that match `url.endsWith('/run')` never fire. Specs that
call the API directly now get `422`.

Passed on main, fail on this branch (re-run on both stacks):

| Spec | Cause |
|---|---|
| `human-approval.spec.js` approve (J11) | run predicate (line 45) and approval predicate (64) miss `?client_id`; raw approval at 79 now 422 |
| `nested-approval.spec.js` approve, decline | `waitForRequest` at 63, 79, 87 uses `endsWith` |
| `routine-run-events.spec.js:52` | raw `page.request.post(.../run)` at 66 returns 422 |
| `a2a-snapshot.spec.js:53` | passes alone on the branch, failed under the 3-worker run (see P3); probably load, not a regression |

Also stale, but they fail on main for another reason (no model) so they are masked:
`delegated-routines` (60, 85, 133), `nested-routines` (16), `model-profiles` (15), `conversation-title`
(raw `PUT /workflows/content` at 111 gets 422), `workflow-author` (raw DELETE, POST pause, POST kanban).

Side effect: spec cleanup calls (`DELETE /api/ui/agent-profiles/object-pw342-*`, `DELETE /workflows/…`)
return 422, so fixtures leak into the database.

Fix: match on `new URL(request.url()).pathname`, and give raw requests a live `client_id` from the page
(or open a stream first). Not done.

Related docs: `docs/LOCAL_STARTUP.md:184` says `curl /api/ui/agent-profiles` returns `202`; it now returns `422`
without `client_id`. This is a breaking change for any external caller and the PR description does not list it.

## P2. Wire and latency problems that remain (same on main, not fixed by the PR)

| Item | Measured |
|---|---|
| `workflow_runs.snapshot` | 28.3 MB, unpaged. Loaded by Dashboard, Workflows (when a workflow opens) and Routines. Dashboard cold load: 30 MB, 7.8 s. Workflows list shows "Loading workflows…" until it lands (4.5 s cold, 10.3 s on main). |
| `connection.ready` | 952 KB on every connect. 950 KB of that is `alerts` (63 alerts, largest 47 KB each). |
| `agent.routine.lifecycle.snapshot` | 1.5 MB on every connect, including an idle Chat tab. |
| `document_types.snapshot` | 554 KB on Routines, once per visit. |
| `status.updated` | 1 MB on main; now gated to the Dashboard. |
| Finite-route acknowledgement time, idle | `GET /workflows` 1.2 s, `GET /runs` 0.8 s, `GET /status` 0.8 s. Under the 3-worker e2e load: average 4–6 s, max 10 s for status, workflows, workflow-runs, widgets, schedules. |
| Backend memory | 3.5 GB RSS (main: 3.7 GB). |
| Backend start | 22 s until the stream accepts connections. |

## P3. Reliability risks

1. **Stream queue is count-bounded, not byte-bounded** (`src/backend/events/routes.py:84`, `maxsize=100`). A
   client that is slow to read multi-MB snapshots is dropped ("Event queue full … closing stale connection",
   107 times during the e2e run). The page reconnects, but events in flight are lost and fixtures seeded by
   specs were missed. This is the likely cause of the load-only failures (`a2a-snapshot`, `journeys-06-10`,
   `routine-run-events:28`, `agent-execution`). [INFERENCE for the exact link; the log lines are observed.]
2. **No visible offline state for the first 30 s.** With the backend killed, the Workflows page stayed fully
   interactive at +9 s with no banner; `+ New` opened the chat drawer. The recovery dialog waits for 30 s of
   silence (`ConnectionRecoveryModal.jsx:12`), and the backend takes 22 s to boot, so a normal restart never
   shows it. On main the Dashboard and Profiles pages show an offline marker; other pages do not.
3. **Reconnect backoff**: after the backend was up again, the Chat tab's first request came 6.7 s later, the
   Inbox tab's 2.4 s later.

## P4. Smaller observations

- `GET /api/ui/model-profiles` is requested twice on `/profiles` cold load.
- `PUT /api/ui/stream/topics` is sent two or three times on `/chat`, `/workflows` and `/channels`. These are
  different topics, not duplicates, but `/workflows` registers three on first paint.
- `GET /api/ui/chats/{id}/messages` returned 404 fifteen times in the e2e log. The pages that issue them were
  not identified (probably deleted fixtures); worth a look if it appears in a normal session.
- A deep link `/chat/<existing id>` ends at `/chat/<id>/new-chat` even after the title loads. The slug is
  cosmetic, but it is wrong. [Not compared with main.]
- Routine editor: the Saving… state never clears for graph autosave and the policy panel shows no "Saved".
  The same happens on main (checked with the same fixtures), so it is not a regression. The graph PATCH is
  acknowledged and its causal `agent.graph.updated` arrives with the right `request_id`; the draft still does
  not settle. Specs `routine-human-node` and `routine-policies:32` fail for this reason on both builds.
  `routine-policies:79` and `model-profiles` expect native `<input>`/`<select>` elements where the UI uses a
  custom listbox; also stale on main.
- `/logs` showed one `502 Bad Gateway` console error under load; not reproduced on a quiet stack.
- `/settings` and `/search` are not routes; they land on a new draft chat.

## P5. Environment and blocked

Not defects:

- Playwright's Chromium was not installed (`npx playwright install chromium` fixed it).
- Specs hard-code `psql … -p 15433 -U progena`. I forwarded `15433` to the stack's database.
- `a2a-timeout` needs `PROGENA_TEST_AGENTS_ROOT`; `inbox-structured` needs `INBOX_SMOKE_DATABASE_URL`
  (16 skipped). `knowledge-research` failed on Jina MCP discovery.
- No model provider: every spec that starts a model turn fails on "The selected provider is not ready for
  model calls" (`routines-01-05`, `journeys-16-20`, `routine-author`, `collaboration`, `model-profiles`
  execution, `delegated-routines:170`, `workflow-author`, `conversation-title`).

## Not covered this session

Detail-pane resync after a stream gap (B4), idle pause and resume (B9) and the schedule picker (B10) were
verified live in earlier sessions on this branch and not re-run here. Staging behind the real proxy, mobile
viewports and the unrun inbox specs remain untested.
