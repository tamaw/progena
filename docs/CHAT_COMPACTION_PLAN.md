# Conversation-summary visibility

Status: implemented; PR #462 reviewed and follow-up fixes verified locally.
Branch: `feat/conversation-summary-visibility`, created fresh from fetched
`origin/main` at `463494e0`.

## Approved scope

Show native conversation summarization in chat. Tool-output offload and old
tool-argument trimming indicators are deferred: neither supplies a verified
native semantic event. Do not add SDK patches or replacement compression logic.

## Verified native behavior

DeepAgents injects summarization through `create_deep_agent()`. Original messages
remain in checkpoint state; effective model input uses a summary plus recent
messages. Model-aware defaults trigger at 85% input capacity and retain about
10%; defaults without profile limits are 170,000 approximate tokens and six
messages. Context-overflow errors can also trigger summarization.

Native summary model calls carry `metadata.lc_source="summarization"`. Verify
public callback propagation through the real graph before using it for live UI.
A model callback finishing is not proof of committed summary state: the native
middleware returns its state command after the subsequent main model call.
Retries must not look like final failures.

Native `_summarization_event` contains `cutoff_index`, `created_at_index`,
`summary_message`, and `file_path`. This is a private SDK field; isolate its
interpretation in one session/runtime adapter. The frontend must not know SDK
keys or parse generated prompt-wrapper wording.

Read-only production investigation found 32 summary writes across 10 threads,
including five nested-agent events. Native rehydration preserved 785 original
messages in one sample. Historical summary lookup took 171 ms and sampled message
reconstruction 72 ms; these are observations, not load benchmarks. Installed local
DeepAgents/LangGraph patch versions differ from deployment; verify the exercised
native dependency contract rather than assume private hooks are stable.

## Presentation

- While the native summary model runs, show `Summarizing conversation…` inline.
- Only committed state yields a persistent `Conversation summarized` marker.
- Expand the marker into a bounded, scrollable, sanitized Markdown summary.
- Place markers at their creation boundary, not the summarized-prefix cutoff.
  Resolve indices to stable message anchors before browser projection.
- Preserve all original visible/searchable conversation history.
- Use existing timeline icons, styled tooltips, keyboard expansion and design
  tokens. Completed markers are not debug-only activity.
- Attribute nested-agent summaries to their agent, not the root conversation.
- Do not claim exact summary duration, token savings or percentages.

## Architecture and implementation

1. Runtime observes native summary generation with public callbacks and the
   existing custom event/content stream. Do not replace native thresholds,
   algorithms, middleware or provider model calls.
2. Session history projects committed native summary occurrences, not merely the
   latest overwritten state value or uncommitted pending writes. Reuse existing
   checkpointer traversal and native delta-channel rehydration.
3. Backend owns browser contracts, stable marker identity and paging. Reuse
   existing checkpoint revision/cache invalidation. Full summaries belong only to
   the requested history/detail, not token frames or chat-list updates.
4. Frontend reconciles live activity with committed history using stable scope and
   identity. Reload, reconnect, terminal refresh and older-page merging must not
   duplicate or lose markers.
5. Preserve the existing canonical `/api/ui/stream` and request-scoped response
   content stream. No third EventSource, polling, parallel ledger, or new database
   notification mechanism.

## Verification

- Exercise real native summarization with a controlled local fake model and
  checkpointer: live activity, committed marker, preserved original messages.
- Verify failed/cancelled calls do not fabricate completed history; callback
  retries and simultaneous/nested scopes remain isolated.
- Verify all committed summary occurrences, stable creation anchors, paging and
  reload/reconnect deduplication.
- Exercise the actual chat UI and `/design` styling, including expansion and
  bounded summary scrolling. Never trigger paid production model turns for tests.
- Run relevant existing regression suites, Python lint/type checks, frontend
  lint/unit checks and production build. Record passed, skipped and unrun checks.

No production writes, migrations, deployments or forced production model turns
are required. Merge and deployment remain explicitly human-authorized actions.

## Implementation and verification record

- Native `AsyncCallbackHandler` observes summary generation through existing
  custom response events. Summary-model tokens never become assistant text.
  Unknown delegated scopes are not mislabeled as root conversation activity.
- Native committed checkpoint state supplies all historical occurrences;
  pending writes and failed post-summary main-model calls do not create markers.
- Browser descriptors use `summary_markers` with stable `id`, `summary`,
  `anchor_message_id`, and `scope: {kind, id, label?}`. Root markers render
  **after** the preceding projected message; null means transcript head.
  Nested scope IDs identify the parent task tool call and render inside its card.
- Summary reads are detail-only and checkpoint-revision cached. A failed read
  does not substitute another revision's summary. Markdown parsing waits for
  first expansion; expanded bodies have their own bounded scroll area.
- A real local native graph produced start/finish activity, one committed marker,
  and only the main-model answer in assistant deltas, preserving original IDs.
- A disposable local Postgres smoke recovered two successive native summaries at
  creation indices 5 and 7, stable after reload, with all original IDs preserved.
  The scratch database was removed.
- Browser smoke used actual chat components with native-projected data: keyboard
  disclosure, collapsed tooltip, original messages, live cleanup and bounded
  scrolling (234px visible versus 2349px content). A separate nested-card fixture
  verified scoped placement. `/design` was inspected. This was not a paid
  full-stack provider turn or a forced production compaction.
- Frontend: ESLint passed, 1,326 unit tests passed, production build passed under
  Node 24. Build reports the existing large-chunk warning.
- Python: Ruff and pinned Pyright passed; 85 targeted runtime/backend regressions
  passed after fixes. The earlier broad run had 2,372 passes, eight skipped
  knowledge-indexer tests requiring `PROGENA_DATABASE_URL`, and two failures.
  The summary assertion was corrected and passed in the targeted rerun. The other
  failure was `routines/test_code_node.py::test_code_node_rejects_oversized_result_inside_the_sandbox`:
  QuickJS raised `DeadlockError` instead of the size-limit `JSError`. That initial
  broad run was not green; the follow-up sandbox fix is described below.

Temporary smoke entry points were removed. No production mutation, migration,
merge or deployment was performed.

## PR review follow-up

PR #462 review identified six actionable issues: ordinary item reads traversing
full checkpoint history, child summaries escaping their root revision, stale
message fallback mixed with exact summaries, unreachable own-turn Quick Chat
reconciliation, dropped markers in persisted promotion, and historical render
churn from equivalent summary arrays.

The sandbox fix leaves the one-second deadline and 1 MiB cap unchanged. It
rejects impossible UTF-16 lengths immediately, then counts additional UTF-8 bytes
using native runs grouped by encoding width rather than scanning each ASCII byte
in JavaScript. Captured native helpers resist guest prototype mutation. A real
routine smoke executed ASCII/BMP near-limit successes concurrently with
oversized-result rejections; both rejections surfaced the size-limit `JSError`.
Exact-limit and one-byte-over boundary coverage includes ASCII, two-byte,
three-byte, astral and mixed Unicode results.

The suggested object-agent middleware replacement was rejected and the reviewer
retracted it: the existing LangChain summarizer has no configured trigger and
does not currently summarize. Enabling it would exceed the visibility-only scope.

### Follow-up verification

- Full Python suite, four workers: **2,385 passed, eight skipped**. The skips are
  the existing knowledge-indexer tests requiring `PROGENA_DATABASE_URL`; the
  sandbox deadlock regression and UTF-8 boundaries pass.
- Frontend: **1,334 passed**; ESLint and the production build pass under Node 24.
  Ruff, formatting checks and pinned Pyright pass.
- A native nested-agent graph using disposable Postgres returned one committed
  child summary at the completed root revision and none at its preceding parent
  checkpoint. Original child messages and marker identity survived reload.
  Checkpoint ancestry is walked in memory from batched native rows, not with an
  additional database round trip for every ancestor.
- Root-revision pinning applies to summary-bearing views. Existing live Runs
  traces continue to observe independently advancing child state and retain
  nested token attribution.
- Browser smoke used real localStorage promotion/write/read and actual chat
  components with native-projected data. It restored one marker, retained array
  identity for equivalent reconciliation, and preserved bounded keyboard
  disclosure without browser errors. Temporary smoke entry points were removed.
- Quick Chat regression coverage exercises own-turn terminal reconciliation,
  stale successor protection, cancelled/failed terminals, persisted promotion
  boundaries and unrelated historical-row render stability.
