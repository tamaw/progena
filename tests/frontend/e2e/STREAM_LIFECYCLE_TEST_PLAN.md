# Stream lifecycle test plan (PR #492)

Playwright checks for the tab-scoped `/api/ui/stream` pattern, reconnect,
request recovery and lazy-loaded lists. Run every part, then **report failures
only** in the format at the end. Do not report passes, do not fix anything,
and do not change data on any shared or production instance.

## Stack

Run against an already-running isolated stack (see `docs/LOCAL_STARTUP.md`):
Postgres, Progena runtime, backend and frontend. Use the production build
(`npm run build` + preview) for the scenario checks; the Vite dev server runs
React StrictMode, which doubles mount effects and request counts.

| Variable | Meaning | Example |
|---|---|---|
| `FRONTEND_BASE_URL` | frontend under test | `http://127.0.0.1:24173` |
| `BACKEND_URL` | backend, for reference reads | `http://127.0.0.1:28000` |
| `PROGENA_DATABASE_URL` | the stack's database (fixtures) | from the stack env |
| `INBOX_SMOKE_DATABASE_URL` | an isolated database for `inbox-structured.spec.js`; never the stack's | `postgresql://…/inbox_smoke` |
| `PROGENA_TEST_AGENTS_ROOT` | installed `progena.agents` checkout (greetings fixture) | — |

### Data

The stack should hold realistic data. A useful set is a copy of a real
instance's `progena.state` documents (chats with their root checkpoint
history, workflows and runs, routines and revisions, agents, instructions,
knowledge bases and revisions, inbox messages, kanban cards and activity,
canvases, repositories, alerts, schedules, document types, policies) plus at
least 120 chats and 117 inbox messages so both lists span three pages. Every
copied schedule entry and workflow must be paused, and channels, provider
credentials and preferences must not be copied.

Record the counts the server reports for: active, unread, archived chats;
all, unread, pending, archived inbox messages. The scenario checks compare the
UI against these.

### Reference reads

"The server's list" means a read made outside the page under test: open a
second browser context, let it connect `/api/ui/stream?client_id=<ref>`, then
`GET` the same list URL the page used with `client_id=<ref>`, `offset=0` and
`limit=200`, and read the correlated `*.loaded` event from that stream.
Compare titles after trimming whitespace.

## Part A — existing suite

```bash
FRONTEND_BASE_URL=… PROGENA_DATABASE_URL=… INBOX_SMOKE_DATABASE_URL=… \
  npm run test:e2e
```

- A spec that **skips** because a variable is unset is not a failure; list it
  once under "Not run" with the missing variable.
- These specs start model turns and need a working model profile:
  `a2a-timeout`, `collaboration`, `delegated-routines`, `inbox-structured`,
  `journeys-06-10`, `journeys-16-20`, `knowledge-research`, `routine-author`,
  `routines-01-05`, `workflow-author`. A failure caused by the provider (auth,
  quota, timeout from the model) goes under "Blocked", not "Failures".
- Every other failing test is a failure. Attach its error and trace path.

## Part B — scenario checks

Each check names its pass condition. Anything else is a failure. Watch the
browser console and network for every check: an uncaught error or a `4xx`/`5xx`
from `/api/ui/*` that the check did not provoke is a failure of that check.

### B1 Page tour

Visit every primary route (Inbox, Chat, Dashboard, Canvases, Insights, Runs,
Tasks, Workflows, Routines, Agents, Instructions, Plugins, Profiles, Tools,
Knowledge, Schedules, Channels, Repositories, and each Settings section).
**Pass:** every page renders its data, no error boundary, no console error,
no `4xx`/`5xx`.

### B2 Tab-scoped audience

Tab A tours ten pages; tab B stays idle on Chat. Measure bytes of `ui.event`
frames each tab receives (CDP `Network.eventSourceMessageReceived`).
**Pass:** tab B receives under 50 KB in total, and no event for a page it is
not on (e.g. `workflows.snapshot`, `tasks.snapshot`, `inbox.list.loaded`).

### B3 Cross-tab live updates

Two tabs on the same page; change data in tab B, observe tab A without reload.

| Change in B | A shows it within 3 s |
|---|---|
| rename a provider (Settings → Providers), then rename it back | Providers list |
| create then delete a schedule | Schedules list and Dashboard "Enabled schedules" (A on Dashboard) |
| edit a workflow node title | open workflow editor |
| pin / unpin / archive a chat | Chat sidebar order |
| mark an inbox message read / unread | Inbox row and nav unread count |

**Pass:** each change appears in A, and B receives exactly one correlated reply
per request (no duplicate snapshot).

### B4 Reconnect with missed changes

Open a detail pane in tab A, block A's stream (`route('**/api/ui/stream?**',
abort)` then `window.stop()`), make the change from tab B, unblock.

Detail panes: workflow editor, agent detail, routine editor, MCP server (Tools),
repository, knowledge page (and its revisions), plugin skill preview, task
activity, open inbox message, open chat.

**Pass:** within 12 s of unblocking, A shows the change, and A re-requested that
detail exactly once.

### B5 Server restart

Two tabs open (Chat scrolled to row 80, Inbox). Restart the backend.

**Pass:**
- both tabs reconnect within 15 s of the backend being up;
- each tab registers its topics before any snapshot request;
- each list requests page 0 once, plus the visible page once;
- each resource (preferences, inbox counts, profiles) is requested once;
- a request accepted before the restart fails with "The server restarted
  before this request finished" rather than hanging.

### B6 Lazy-loaded lists

For the Chat sidebar and the Inbox, under each filter (All/Active, Unread,
Error, Archived, Pending), a search, and Name sort: scroll top to bottom in
steps of 80 % of the viewport, waiting 700 ms per step.

**Pass:** no placeholder row remains visible after settling, and the rendered
titles in order equal the server's list exactly (same count, same order).

Then, with the Chat sidebar scrolled to row 80, from tab B: pin row 100, unpin
it, archive row 60, unarchive it, archive row 110, unarchive it. After each,
**pass** if the first six visible titles are unchanged (the viewport stays
anchored), and a final sweep still equals the server's list.

Switch filter while scrolled to the bottom of a longer view (Inbox Unread →
Pending). **Pass:** only `offset=0` of the new view is requested.

Leave Chat while scrolled, change chats from tab B, return. **Pass:** the list
equals the server's list after a sweep.

### B7 Chat routes

- Bare `/chat` with no last-open chat. **Pass:** URL becomes
  `/chat/<id>/new-chat` and no `GET /api/ui/chats/<id>` is sent.
- Deep link to a copied chat with history. **Pass:** its transcript renders.
- Deep link to a chat outside the first sidebar page. **Pass:** header title,
  AI Rename and Open in Quick Chat are available.

### B8 Finite request contract

For a sample of routes (`GET /api/ui/workflows`, `PATCH /api/ui/providers/{id}`,
`GET /api/ui/alerts`, `PUT /api/ui/preferences`):
**Pass:** no `client_id` → `422`; a `client_id` with no open stream → `409` and
nothing changes; a live `client_id` → `202` with `request_id`.

### B9 Idle pause and resume

Skew `Date.now` forward 31 minutes in a tab. **Pass:** "Updates paused" shows,
input does not dismiss it, Resume opens a new stream, re-registers topics, and
a change made during the pause appears.

### B10 Schedule destination picker

Open New schedule. **Pass:** the destination list shows the 50 most recent
active chats in server order and a "N more" hint; searching finds the oldest
chat; a chosen chat stays selected after clearing the search.

## Report format

Report only what failed. If nothing failed, write `No failures.` followed by
the "Not run" and "Blocked" lists.

```markdown
## Failures
### <check id or spec name> — <one-line summary>
- Expected: <pass condition>
- Actual: <what happened>
- Evidence: <request/response, console line, screenshot or trace path>
- Repro: <minimal steps>

## Blocked
- <spec or check>: <reason, e.g. model provider 401>

## Not run
- <spec or check>: <missing variable or prerequisite>
```
