# PR #349 review issues

Source: 16-reviewer static review of `feat/workflows` @ `f9e6016d`, plus follow-ups.
Findings are unreproduced until fixed; reproduce each before fixing.

## Decisions (2026-10-14)

Rule for every fix: clean cutover. No legacy handling, fallbacks, aliases, or compatibility shims.

| Item | Decision | Also resolves |
|---|---|---|
| P1-1 | Fail fast, no retry. Workflow-level failures → failed `workflow_run` (agent can read and fix). Event-system failures (dispatcher/handler/store exceptions) propagate: cursor not advanced, dispatcher stops loudly. | — |
| P1-2 | Add `txid xid8 DEFAULT pg_current_xact_id()` to `document_events`; read only `txid < pg_snapshot_xmin(pg_current_snapshot())`; delete the grace timer. Single backend consumer. | P2-4 (seq no longer inferred from retained rows) |
| P1-3 | Fix: normalize invocations through declared trigger ports. | — |
| P1-4 | **Known issue, acknowledged.** Not fixed in this pass. | — |
| P1-5 | Remove the `human_input` node kind entirely. Workflows keep `approval`; routines own HITL (`human` node). | P1-5 bug itself |
| C-1 | f-string `SESSION_KEY_PREFIX` into SQL; type ids stay literals. | — |
| P2-25 | Delete `WorkflowSchemaEditor` and `WorkflowReferencePicker`. | P2-22, P2-23, P2-24 |

When a fix resolves another listed item, tick that item too and note `resolved by <ID>`.

Branch: `fix/pr349-review` (from `792b3c4b`, session documents).

## Validation (2026-10-14, current code, nothing reverted)

| Status | Items | Evidence |
|---|---|---|
| Validated by test | P1-1, P1-2, P1-3, P1-6, P1-7, P1-8, P2-1–P2-16, P2-27, C-1 | 70 targeted tests pass (`-rA`), incl. Postgres late-commit, rollback and profile-race tests on the scratch DB |
| Validated live | P1-3 | invoke request on dev stack: run `completed`, source `progenitor`, agent step ran, request deleted |
| Validated live (browser) | P1-9, P1-10, P1-11, P2-18, P2-20, P2-26, P1-5 (UI) | false value + stored size saved and reloaded; type change enables Save; Configure after Apply JSON; Unpack follows a palette node's schema change; dialog scrolls; palette has no Human Input |
| Validated live after a second fix | P2-19, P2-21 | first fix did **not** resolve them (Cut left the edge in the draft and saved file). Cause: graph handlers ran side effects inside state updaters. Rewritten to compute from refs; canvas and draft now drop the edge and Unpack fields. Saving that graph is correctly refused (Unpack with no input) |
| Validated by e2e | P2-28, P2-29, P2-30 | specs pass; no `pw-` workflows, cards or `card_order` refs left |
| Validated live (browser, 2026-10-15) | P1-12 | real quick chat pane (not the main chat; the first attempt had used the main chat input). Closed mid-reply at 359 chars, before the end token, and the pane unmounted. The turn completed server-side 6 s later. Reopened: full 7,924-char reply with the end token, no "Thinking", New chat/Open in chat enabled, no Stop button, Send enables with text |
| Validated live (browser, 2026-10-15) | P2-17 | roster request aborted on load, so `@` showed no agents; unblocking alone didn't retry. A backend restart dropped the stream; each new stream session re-requested the roster (one 500 while the backend started, then 202), and `@` listed all 4 agents again |
| Deferred | P1-4 | known issue |

## P1

- [x] **P1-1 Handler failure advances the event cursor** — `app/backend/document_events.py:248-257`. Fixed: handler exceptions propagate, the cursor stays on the failing event and the dispatcher stops (`DocumentEventDispatcher.stopped`); no retry loop.
- [x] **P1-2 Timed-out sequence gap treated as rollback** — `app/backend/document_events.py:208-215`. Fixed: `document_events.txid` + commit-order position `(txid, seq)`; reads only transactions older than the snapshot xmin. Grace timer deleted.
- [x] **P1-3 Progenitor-invoked event workflows run nothing** — `app/backend/ui_routes.py:1766-1771`, `app/backend/workflow_service.py:717-723`. Fixed: invocations go through `build_manual_trigger_event` and are validated against the trigger's ports (validation bypass removed).
- [ ] **P1-4 Agent document writes lack run origin → self-trigger loop** — **Known issue, not fixed** (decision). `app/backend/workflow_service.py:1107-1114`. Only `save_workflow_run` enters `document_write_origin(run.run_id)`. Fix later: carry run id across the prompt-runner/runtime boundary into agent write transactions.
- [x] **P1-5 Human-input pauses routed through approval** — resolved by removing the `human_input` node kind (model, schema, service, UI, skill).
- [x] **P1-6 Model profile PATCH/DELETE lost updates** — Fixed: `mutate_document` under a transaction-scoped advisory lock on the key; interleaving tests on Postgres.
- [x] **P1-7 Agent-written workflows can't use prompt references** — Fixed: the `/workflows/` mount snapshots prompt documents and passes a loader.
- [x] **P1-8 `resolve_type_ref` drops `$defs`** — Fixed: returns `(schema, definitions)`; callers hoist definitions to the composed port root.
- [x] **P1-9 Falsy data values become null** — Fixed (`??`).
- [x] **P1-10 Schema edits don't mark dirty** — Fixed by one shared `nodeCallbacks` set for every node (also P1-11, P2-20).
- [x] **P1-11 Apply JSON misses node callbacks** — resolved by the shared `nodeCallbacks` (P1-10); applying JSON also marks the draft dirty.
- [x] **P1-12 Quick chat stuck streaming after reopen** — Fixed: a remount with a cached streaming turn and no running generation reloads the transcript.

## P2 — backend

- [x] **P2-1 Cron resume replays paused interval** — Fixed: cron state records `active`; a paused→active transition restarts counting from now.
- [x] **P2-2 Cached cron clock bound to old event loop** — Fixed: `start()` recreates the wake event.
- [x] **P2-3 Pause during awaited node overwritten** — Fixed: the checkpoint rereads the persisted run and keeps a concurrent pause; regression test restored.
- [x] **P2-4 In-memory event seq reuses numbers after pruning** — Fixed: allocation counter independent of retained rows (and cursors are `(txid, seq)` positions, see P1-2).
- [x] **P2-5 Profile delete leaves whitespace-alias key** — Fixed: the validator rejects names that collide after whitespace normalization; snapshot validated before the write commits.

## P2 — workflow model (`app/progenitor/workflows.py`)

- [x] **P2-6 Extract inference drops root `$defs`** — Fixed (definitions kept under their keys at the result root).
- [x] **P2-7 Nullable/union type compatibility uses inequality** — Fixed: containment, mirrored in the editor's `schemasAreCompatible`.
- [x] **P2-8 `True == 1` hides changed_fields** — Fixed: JSONB equality (`true ≠ 1`, `1 = 1.0`) for changed_fields and event match.
- [x] **P2-9 `filter_fields: 42` raises TypeError → 500** — Fixed: must be a list of strings (absent = none).
- [x] **P2-10 Cycle test never reaches cycle detection** — Fixed.

## P2 — prompts, schemas, skill

- [x] **P2-11 Non-string YAML keys raise TypeError** — Fixed.
- [x] **P2-12 Non-`.md` prompt paths writable but unopenable** — Fixed: one `prompt_relative_path` rule used by the mount, the prompt API and prompt references.
- [x] **P2-13 `workflow_run` schema rejects `source: "cron"`** — Fixed.
- [x] **P2-14 Skill advertises `/prompts/...` paths loader can't resolve** — Fixed: references are paths relative to `/prompts/` (skill updated); a `/prompts/...` reference is rejected as invalid.

## P2 — frontend store / chat

- [x] **P2-15 Snapshot-save timeout restarts on unrelated renders** — Fixed: deadline effect keyed to the acknowledged request only.
- [x] **P2-16 Cancelled save error lands on new selection** — Fixed.
- [x] **P2-17 Failed agent-profiles fetch never retried** — Fixed: requested once per realtime stream session, so a reconnect retries.

## P2 — workflow editor

- [x] **P2-18 Saved node sizes dropped on hydrate** — Fixed.
- [x] **P2-19 Cut bypasses Unpack dependency cleanup** — Fixed: Cut goes through `onEdgesChange`.
- [x] **P2-20 Palette-added nodes don't recompute Unpack schemas** — resolved by the shared `nodeCallbacks` (P1-10).
- [x] **P2-21 Removing unused Unpack input doesn't `setGraphNodes`** — Fixed.
- [x] **P2-22 Schema editor drops `additionalProperties`/`$defs`** — resolved by P2-25 (component deleted).
- [x] **P2-23 Property rename remounts input each keystroke** — resolved by P2-25.
- [x] **P2-24 Rename onto existing property overwrites it** — resolved by P2-25.
- [x] **P2-25 `WorkflowSchemaEditor` / `WorkflowReferencePicker` unused in production** — Deleted, with `store/uiData/workflowReferences.js` and the schema-editor CSS.
- [x] **P2-26 Options dialog body clipped** — Fixed: the dialog is a flex column and its body scrolls within it.
- [x] **P2-27 "Flags incompatible edges" test asserts nothing about edges** — Replaced with compatibility tests against `schemasAreCompatible`.

## P2 — e2e

- [x] **P2-28 `discovery.spec.js` clicks nonexistent `/^Subagent/`** — Fixed.
- [x] **P2-29 `workflow-author.spec.js` accepts any card's run** — Fixed: the authored event node matches `after.title`, and the test selects that card's run.
- [x] **P2-30 `workflow-author.spec.js` teardown leaks runs/chats/`card_order`** — Fixed: deletes the quick chats, runs and run threads; the card goes through the card route.

## Cleanup — session key literals

- [x] **C-1 Use `SESSION_KEY_PREFIX` instead of `"session:"` literals.** Done; the SQL bodies substitute the constant (the event trigger uses the file's existing `.replace` placeholder style, the session trigger an f-string). Type ids stay literals.

# Round 2 (2026-10-16)

Second review of the PR head `d45780b1` (16 reviewers; model profiles and store/dashboard came back clean). Fixed on `fix/pr349-review-2`. Each item has a test that fails before the fix unless noted.

## P1

- [x] **R2-1 Dispatcher stalls behind the xmin watermark** — `document_events.py`, `document_store.py`. A committed event held back by an older open transaction was never re-read once that transaction ended without an event of its own. `has_deferred_document_events(after)` reports such events; the dispatcher then re-checks every second (`_DEFERRED_RETRY_SECONDS`) instead of waiting only for NOTIFY. Tests: Postgres `test_events_behind_an_open_transaction_are_reported_deferred`, dispatcher `test_dispatcher_rechecks_while_events_are_deferred`.
- [x] **R2-2 Session event handler loaded the transcript** — `ui_routes._publish_chat_session_document_event` now builds the sidebar event from `chat_sessions.get_chat_summary`, which reads history best-effort (runtime down → `history_unavailable`), so a rename during an outage reaches the sidebar and can't stop the dispatcher. Found in the live check: `PATCH /api/ui/chats/{id}` also loaded the transcript just to check the chat exists, so that rename applied but answered 503; it now checks the session document only.
- [x] **R2-3 `/sessions/` mount had no `aglob`** — `SessionDirectoryBackend` inherits `_AsyncOnlyFileBackendMixin` (glob, grep, download); its own grep is gone.
- [x] **R2-4 Session field values unvalidated** — writable fields are checked against the session schema (`DocumentType.validate_write`, shared with the `document_write` node) before merging; `title: 123` is rejected.
- [x] **R2-5 + New during streaming left the new chat disabled** — `startNewChatSession` backgrounds the old response and retires the foreground turn (`useChatStream.retireForegroundStream`) without cancelling it.
- [x] **R2-6 Cron fires once per backend** — each due time is claimed with `mutate_document` (re-evaluated against the stored state under the row/advisory lock); only the claimant fires.

## P2 — backend

- [x] **R2-7** `mutate_document` reads with `SELECT … FOR UPDATE` (advisory lock kept), so plain saves/merges can't commit between its read and write. Postgres interleaving test; fails without `FOR UPDATE`.
- [x] **R2-8** In-memory store: writes, event allocation/append and event reads share one reentrant lock. No deterministic test (thread interleaving inside a deep copy); covered by the existing suite.
- [x] **R2-9** In-memory store applies the Postgres session noise filter (updates touching only `content`/`last_activity` log no event) on save, merge and mutate.
- [x] **R2-10** A `document_write` with nothing connected to write outputs `{"found": …}` only.
- [x] **R2-11** `{thread_id}` in an Agent/Extract template renders the run's thread when the port isn't wired.
- [x] **R2-12** Agent/Extract `thread_id` outputs the thread the runner actually used.
- [x] **R2-13** Data-node values are refreshed from each reload of the definition, so a mid-run edit reaches later nodes.
- [x] **R2-14** `document_query` filters use JSON equality (`json_equal`, formerly `_json_equal`): `true` ≠ `1`.
- [x] **R2-15** Unpack resolves a local `$ref` (`#/$defs/…`, `#/definitions/…`) before deriving its ports.
- [x] **R2-16** A nullable object (`["object", "null"]`) is unpackable.
- [x] **R2-17** An `integer` output connects to a `number` (or `["number", "null"]`) input.
- [x] **R2-18** Pausing/disabling a workflow marks its cron state inactive from the workflow's document event (`WorkflowCronClock.workflow_changed`), so a pause and resume between two ticks still counts from the resume.
- [x] **R2-19** Cron state keys percent-encode the workflow and node ids (`a:b`/`c` ≠ `a`/`b:c`). State under an old key is removed by the clock and the node starts counting from now.
- [x] **R2-20** Only an exact `---` first line opens prompt front matter (`----` is body text).
- [x] **R2-21** Embedding a type ref hoists the targets of other local refs (e.g. `#/properties/name`) into `$defs` and rewrites the refs; a self-reference to the root is rejected.
- [x] **R2-22** `first_message` drops the attachment note (new SQL `progenitor_session_first_message`, used by the trigger and the now-retired session migration; an attachment-only opener reads `* sends an attachment`), and the fallback title is secret-redacted (`display_title`). `ATTACHMENT_CONTEXT_MARKER` moved to `app/progenitor/sessions/titles.py`. Sessions created before this keep their stored `first_message`.
- [x] **R2-23** Transcript renders of one thread never overlap: a turn finishing mid-render queues one more render (`RuntimeChatService._render_session_until_current`).
- [x] **R2-24** Session `edit_file` uses `perform_string_replacement`: an ambiguous match fails unless `replace_all`.
- [x] **R2-25** A routine subagent resolves one graph snapshot (`resolve_routine_graph`) and passes it to the executor, so input validation and execution use the same graph.

## P2 — frontend

- [x] **R2-26** A local draft becomes the selected owner once its session appears, so reconciliation runs for it.
- [x] **R2-27** Reopened quick chat rebuilds the streaming reply from an empty replay accumulator (no duplicated prefix).
- [x] **R2-28** Opening a different unknown thread clears the transcript and releases the foreground stream; only reopening the same draft keeps it.
- [x] **R2-29** Editing an Extract template changes only its inputs; the typed result and its Unpack edges stay.
- [x] **R2-30** Selecting a prompt document (including via the radio) loads it and derives its input ports. The Workflows page now loads the prompt catalogue (before, the picker was empty unless the Prompts page had been opened), and the content fetch/derive runs once per path and text: the first version re-dispatched on every render and crashed the page with "Maximum update depth exceeded" (found in the browser check).
- [x] **R2-31** Deleting a node prunes dependent Unpack edges down the chain (with the same confirmation as removing the edge).
- [x] **R2-32** Palette nodes start compact, so a resize persists.
- [x] **R2-33** Derived prompt inputs keep an optional `thread_id`.
- [x] **R2-34** The node menu is a flex column whose category list shrinks and scrolls (no clipped last items on short screens). CSS only.
- Editor parity with R2-15–17: Unpack derivation and connection type checks accept nullable objects, `$ref`'d objects and integer→number.

## P2 — e2e

- [x] **R2-35** The title workflow matches `after.first_message`, so only the test's chat fires it.
- [x] **R2-36** Teardown removes the session, runs and checkpoint rows, also on failure.
- [x] **R2-37** The title assertion targets the session row in the sidebar, before and after reload.
- [x] **R2-38** Workflow-author teardown deletes the quick-chat threads' checkpoints.

## Validation (2026-10-16)

| Status | Items | Evidence |
|---|---|---|
| Validated by test | R2-1–R2-4, R2-6, R2-7, R2-9–R2-33 | Python 2088 passed / 20 skipped (scratch DB set); vitest 924 passed; ruff, pyright `app/` (0 errors), eslint clean. Regression tests fail on the old code (checked by reverting for R2-7 and R2-15–17) |
| Validated live | R2-2 | runtime stopped: rename `PATCH` → 202 (was 503), `chat.session.metadata.updated` with the new title and `history_unavailable: true` on `/api/ui/stream` |
| Validated live (browser) | R2-5 | streaming a 500-word reply, + New: empty new chat, composer enabled, no Stop button; the old chat finished in the background (582 words on reopen) |
| Validated live (browser) | R2-26 | a new chat's card is selected from the first send through completion; transcript after reload matches |
| Validated live | R2-3 | Progenitor's `glob("*.md", "/sessions/")` returned 67 matches |
| Validated live (browser) | R2-30, R2-33, R2-34 | Prompt document radio picks the first prompt and the node shows its `agent_name`, `log_dir`, `name` ports plus `thread_id`; at 500 px high the node menu's last item (Approval) is reachable |
| Validated live | R2-19 | the dev clock removed a deleted workflow's cron state |
| Validated by e2e | R2-35–R2-38 | `conversation-title`, `workflow-author`, `discovery` pass (27 tests). The first `workflow-author` run failed on the new cleanup query (unquoted `'`, then quick chat's page-context prefix); fixed. No `pw-` workflows, cards or test chats left from these runs |
| Not exercised live | R2-8, R2-18, R2-22 (attachment), R2-23, R2-25, R2-27, R2-28, R2-29, R2-31, R2-32 | unit/integration tests only |
