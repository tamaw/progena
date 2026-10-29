# Claude ACP waiting-token reduction plan

## Status and decision

Implemented and verified locally. Branch: `feat/claude-acp-token-reduction`, based on fetched `origin/main` at `43855dcf`. Deployment is separate from PR publication; see the PR for current commit and CI status.

User selected **actionable events only**: Progena should not periodically spend model turns reviewing a normally running Claude task. Wake for completion, failure, a question/approval, or a detected stall. Manual inspection remains available.

Keep ACP and the current backend supervisor. Preserve cancellation, steering, approvals, failure handling, restart interruption semantics, and exact build/publication evidence. Do not reduce Claude's model or reasoning effort, install the unofficial timer preload, or add a new broker/queue architecture.

## Verified current behavior

- `src/progena/acp/client.py`: `ACPSessionClient.session_update` appends all agent-message chunks to `text_parts`; `get_output` joins them. Thoughts/tool metadata are separate. Collected text is not guaranteed to be only the final answer.
- `src/progena/acp/runner.py`: `ACPRunner.run` awaits ACP asynchronously and returns the collected output. Receiving ACP traffic does not itself spend another Progena model turn.
- `src/backend/workflows/acp_supervisor.py`: the backend owns execution, progress persistence, pending inputs, watchdog observations, and terminalization. These operations do not invoke the Progena LLM.
- `src/progena/tools/acp_sessions.py`: `acp_status` can wait up to 300 seconds, reading task state every two seconds. Its signature already ignores ordinary streamed progress. After it returns, choosing to call it again requires another model inference.
- Status returns up to 4,001 characters of `output`, another 4,001 of `progress.last_message`, and the full plan array. A direct smoke using the real `_snapshot` and `ACPTaskRecord` returned **9,163 JSON characters** for a synthetic working task with 110,000 output characters, an 8,000-character latest message, and 20 plan steps; unchanged calls repeated the same result. This measures characters, not billable tokens.
- `acp_list` can include a 4,000-character summary/output per row.
- `acp_start` sets `notify_on_completion=False`, despite recording the originating thread. The existing terminal collector in `src/progena/orchestration/nodes/acp.py` can already enqueue a same-thread follow-up for tasks opting into notifications.
- `src/progena/acp/tasks.py`: completion follow-ups include the original prompt and up to 1,000 characters of output. That duplicates context beyond the status-tool path.
- Chat does not automatically wake on new `pending_input`; the supervisor persists the input and waits for an answer. Workflow document events can already react without model polling.
- Workflow/routine guidance already recommends document events instead of model-driven `acp_status` polling (`data/skills/managing-workflows/SKILL.md`, `data/skills/managing-routines/SKILL.md`). Live Build result workflow `workflow_d5b163be0ef9` was reported to consume `{task.output}`; inspect its current stored definition before migration, not only checked-in skills.
- Native Claude JSONL transcripts exist in the live container under `/home/claude/.claude/projects/<encoded-cwd>/`. The config directory is persisted, but not currently accessible through a task-scoped application reader. Do not assume ACP session IDs equal filenames without verifying real records.

## Implementation sequence

### 1. Make waiting event-driven for chat as well as workflows

Reuse the existing supervisor, task documents, heartbeat collector, and thread routing.

- Opt chat-originated `acp_start` tasks into the existing completion route; preserve workflow-owned routing so the same result does not trigger both a workflow and a chat reaction.
- Extend that collector narrowly for **new pending input** and **a newly detected stall**, rather than adding a separate event system.
- Persist per-turn material-event acknowledgement identities. Input identity includes `pending_input.id`; stall identity distinguishes a new stall episode after resumed activity. Ordinary text, tool updates, plan progress, and timestamps never count as wake events.
- Ensure late/repeated observations do not repeat the same completion, question, or unchanged stall. A new turn or genuinely new question can wake again.
- Reconcile already-running chat tasks so they do not require continued polling after cutover. Preserve explicit workflow notification ownership and never replay already-handled historical terminal tasks.
- After delegation, end the supervising turn and wait for these events. Change tool descriptions and the relevant agent/workflow instructions together; do not describe a 300-second long poll as fire-and-forget supervision.
- Keep the established restart behavior: queued work can start; active work interrupted by backend restart becomes `interrupted`, not falsely completed or silently resumed.

Existing runtime input delivery uses an in-memory queue and `notification_sent_at`; do not claim exactly-once or crash-proof delivery. Retain existing reconciliation and dedup guarantees; a generic durable-outbox redesign is not part of this change.

### 2. Separate cheap observation from explicit evidence reads

- Default `acp_status`/`acp_list` responses carry task/turn/status, activity age, stalled state, bounded last-tool name/count, error classification, pending-input identity, and a stable result reference.
- Do not include collected output, repeated narrative progress, full plans/history, or tool-call arrays by default. Target **at most 1 KiB serialized JSON for ordinary single-task status**; actionable input/evidence is retrieved explicitly rather than silently truncated to meet that target.
- Add explicit, bounded, cursor-based result/detail retrieval by task and turn. Reuse the compact-list/detail pattern in `src/progena/tools/schedule_results.py`.
- Completion/input/stall follow-ups contain only routing IDs, the material transition, the actionable question/error where needed, and a result reference. Do not repeat the original prompt or dump the transcript into the follow-up.
- Keep existing detailed UI visibility separate from model-facing projections; reducing model context must not make the user's logs disappear.

### 3. Preserve exact evidence outside default model context

- Validate actual Claude transcript/session/turn correlation, including resumed sessions and multiple turns, before using native logs as the source of truth. Final assistant output must not be guessed by splitting text on prose markers.
- Provide task-bound, bounded read access to exact results/transcript ranges. Prefer existing native Claude logs over writing a second copy of every Claude transcript or asking a model to summarize all of it.
- If native transcript reading needs a deployment mount, mount only into the backend, read-only; never expose the Claude config tree to the runtime's generic filesystem tools. Resolve only verified task/session transcript files server-side and reject traversal, symlink escapes, and unrelated sessions/configuration.
- Preserve generic ACP support and older task evidence. Return explicit detail availability; do not invent a transcript reference when none can be verified. Existing stored output remains readable where that is the only retained evidence.
- Once the exact-detail source is proven, bound message/thought/tool-metadata accumulation at collection time. Use capped counters/previews rather than unbounded arrays or repeated whole-output joins. Preserve full required terminal evidence through explicit detail retrieval, not by silently dropping it.
- Audit every output consumer before changing persisted meaning: backend result API/UI, completion transcript insertion, workflow templates, live stored workflows/routines, and publication evidence checks. Migrate affected consumers together; do not add deprecated aliases or parallel old/new orchestration conventions.

### 4. Migrate instructions and active workflow handoffs

- Remove guidance and workflow steps that periodically ask a model to check whether Claude is done.
- Route normal running/progress updates through backend/UI state only. Terminal/input/stall events are the only automated supervisor wakeups for waiting tasks.
- Replace full `{task.output}` prompt interpolation with compact terminal fields and references. Read the selected build evidence once when validation/publication actually needs it.
- Preserve required commit IDs, changed-file scope, check commands/results, PR references, and failure details. Do not weaken publication checks to accommodate smaller handoffs.
- Live workflow/routine changes are a separate explicit deployment mutation: capture definitions first, apply only affected changes, and verify stored readback. Planning does not alter production state.

## Verification and acceptance

1. Exercise the real waiting/collector path with scripted ACP events over a long simulated task. **Zero supervisor LLM invocations caused by ordinary waiting/progress**, regardless of streamed text volume. Unrelated scheduled/user work is excluded from this count.
2. Exactly one normal-handling wake for each terminal turn, new input, or stall episode; repeated scans/progress do not wake again. User messages and explicit manual status remain responsive.
3. Questions/plan approvals remain answerable without polling. Cancellation works while running or waiting for input; errors, usage limits, timeout, and restart interruption remain distinguishable.
4. Compare baseline and new ordinary status/completion payload sizes on identical fixtures; prove the single-task status budget and demonstrate that increasing transcript size does not increase default model-facing response size. Do not invent a percentage token saving from character counts.
5. Retrieve exact final build evidence and selected historical-turn evidence on demand. Verify truncation/pagination and honest unavailability, including native Claude session correlation and generic/legacy ACP records.
6. Test transcript-reader authorization boundaries and bounded reads, including symlink/traversal/cross-task attempts. Never read credential files in smoke tests.
7. Exercise a real Claude ACP session through Progena: start, receive progress without supervisory check-ins, handle input if requested, receive one terminal handoff, then inspect exact evidence explicitly. Measure actual supervisor model requests and token usage against the current polling workflow before claiming savings.
8. Run focused existing ACP client/runner/tool/supervisor/collector tests and affected contract tests once integration is complete. Update `docs/ACP_AGENTS.md`, relevant workflow/routine skills, public task schema if changed, and changelog.

## Decisions resolved / defaults

- **Resolved by user:** actionable-event supervision only; no periodic model reviews.
- Keep Claude's intelligence and native tools unchanged. The primary objective is eliminating supervisor waiting turns and duplicated context, not making Claude less capable.
- Preserve detailed user-visible logs and exact build evidence; access them on demand instead of injecting them by default.
- Implementation accepted by the user. Production deployment remains separate from this branch's implementation and verification.

## Implementation evidence

- Full backend/runtime regression suites: **2,245 passed, 23 skipped**. The known environment-sensitive `tests/backend/test_ui_feedback.py::TestStateReaderFileOperations::test_read_health_with_no_document_store` was explicitly deselected because the existing development runtime occupies port 9501.
- Ruff passed; production Python type checking reported zero errors/warnings. Compose resolved the native transcript mount read-only on the backend only, with anonymous generic captures on the existing disk-backed logs volume.
- Real live Claude ACP producer, exercised through the changed local supervisor: two turns, 70 combined waiting scans, **zero progress/waiting wakeups**, and one terminal wakeup per turn. Exact native output was read through the new local HTTP API and `acp_get_result`, including small paginated reads and isolated retrieval of turn 1 after turn 2. The smoke copied only its own session transcript into a temporary read-only-source fixture; production services were not replaced or restarted.
- Identical synthetic status fixture: **9,163 → 281 serialized characters**, with its 110,000-character stored output still intact. This is a payload measurement, not a billable-token saving claim.
- Generic ACP output remains exact after the in-memory preview fills: private anonymous disk capture holds the stream until result materialization. Once native evidence is verified, capture is released and the prefix stays bounded. Missing native mounts cannot activate truncation; loss of verified final evidence fails explicitly instead of publishing partial output as complete.
- Protocol/collector/supervisor coverage exercises questions, approval, cancellation, errors, stalls, deduplication, active legacy chat adoption, historical-turn boundaries and transcript access controls.

### Production handoff adjustment

The three stored workflow `{task.output}` format nodes are deterministic handoffs, not model prompts; they remain intact so the strict publication code gate receives exact evidence. The publication routine `routine_2f18259a631c` repeated that output in its context-reader agent prompt and its final publisher prompt. A schema-validated, rendered, compare-and-swap update removed only the first copy. The exact code gate and the final publisher's explicit evidence/PR-body input were unchanged. Independent stored readback matched the intended single-node change. Before/after definitions are saved as `local://acp-token-publication-routine-before.json` and `local://acp-token-publication-routine-after.json` in the implementation session.

The context prompt change is live; the event-driven runtime/backend implementation requires PR review, green CI and deployment in a safe idle window. No token-reduction flags, model downgrade or unofficial preload were installed.
