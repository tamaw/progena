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
