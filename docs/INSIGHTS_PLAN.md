# Insights: checkpoint-backed profile usage and estimated cost

## Direction

Build **Spectate → Insights** at `/insights`, with the existing top-bar panel as a compact summary linking to it. Both surfaces use the same PostgreSQL aggregation and request/SSE contract.

Use the existing checkpointing database as the starting source for token usage. Add estimated pricing to `profiles.json`. Do not introduce a second provider-call ledger or broad instrumentation project unless inspection of stored records demonstrates a requirement that the checkpoint-backed design cannot meet.

This records the checkpoint-first design following the user's clarification that token records are in the checkpointing database and pricing belongs in model profiles. Implemented on `feat/insights`; operational contracts and limitations are documented in `BACKEND.md`, `SPEC.md`, and `STATE_DOCUMENTS.md`.

## Evidence and remaining checks

- `app/frontend/src/components/layout/AppRuntimeShell.jsx:1884-2009`: the current panel derives values from widget indexes/cycle counts. Range is local state; Refresh fetches widgets. Browser inspection confirmed unchanged usage when switching 7d to 30d.
- `app/progenitor/runtime/messages.py`: usage parsing already handles provider input/output/total and separate reasoning/cache metadata.
- `app/backend/chat/service.py`: checkpoint message projection reads provider usage; its display token count is not a full accounting metric.
- `app/progenitor/routines/factory.py`: routine `run_meta` contains token summaries. Workflow totals also exist; neither should be added to the same underlying messages again.
- `app/progenitor/state/model_profiles.py:58-79`: profile resolution currently returns a model without explicitly preserving the profile name. Inspect persisted messages/run context before deciding which attribution metadata must be added.
- Profile names are mutable catalog keys. Deployment can change; multiple profiles can target the same deployment. Today's deployment mapping alone is not historical proof of a profile.
- `app/backend/settings/model_profiles.py`: public fields and PATCH fields are explicitly whitelisted, so pricing must be added to validation, public projection, mutation and the Profiles UI—not merely stored as ignored JSON.
- UI data follows acknowledged HTTP requests and correlated SSE snapshots. Database setup uses idempotent DDL and pooled Psycopg queries.

The first implementation task is a read-only inspection of actual checkpoint records/schema: message identity, usage metadata, profile/model context, timestamps, nested threads, repeated checkpoints and retention. Do not print message content or secrets. Static tracing identified possible gaps in auxiliary/direct-client paths, but this does not establish the contents or completeness of the user's actual database.

## Pricing schema

Proposed optional object on each named profile:

```json
{
  "pricing": {
    "currency": "USD",
    "input_per_million": "2.50",
    "cached_input_per_million": "1.25",
    "output_per_million": "10.00"
  }
}
```

The numbers above illustrate the schema only; they are not verified prices for any deployment. Actual defaults must be populated from verified model/provider pricing, with deployment-to-model mapping established, or explicitly supplied estimates. Do not guess an underlying model from an arbitrary Azure deployment name.

Use nonnegative decimal rates and decimal arithmetic, not binary floating point. Currency is explicit; do not sum different currencies into a single amount. Missing pricing is unpriced, not free. An explicit zero rate means zero cost.

Estimated cost = ((input - cached input) × input rate + cached input × cached-input rate + output × output rate) / 1,000,000.

Cached tokens are a subset of input. Reasoning is generally part of output and must not be charged twice. Normalize provider-specific semantics before applying the formula. If cached usage or a required rate is absent, expose the missing detail or an explicitly labelled approximation; do not silently claim an exact cache discount.

Initial reporting uses **current configured rates**, labelled "Estimated cost at current profile rates". Editing a rate intentionally recalculates historical estimates. This is simpler than pretending the app has historical billing tariffs. If historic-as-incurred cost becomes a requirement, persist the applicable pricing revision at invocation time; do not infer past rates from the present catalog.

Populate supported profile prices and expose editable rate fields in Profiles. Deleted/unattributed profiles remain visible in token history, but are unpriced unless a reliable pricing reference exists. No API keys, endpoint credentials or arbitrary private profile fields are exposed.

## Checkpoint accounting contract

1. Aggregate unique provider/model message usage, not every checkpoint version containing the same message.
2. Establish stable deduplication identity from actual records: provider response/message identity, thread/checkpoint lineage and nested graph behavior. Do not assume all message IDs are globally unique. Repeated/growing partial records must resolve to the authoritative final usage rather than summing snapshots.
3. Do not add workflow/routine/turn summaries to message usage they already summarize. Count all distinct underlying model calls, including tool rounds that genuinely issue another provider request.
4. Preserve input/output/total separately, plus cache-read and reasoning details when reported. Do not add cache/reasoning subsets to total. Derive input + output only when both are known and total is absent.
5. Establish timestamps from the recorded call/message context, not a later checkpoint write that copies old messages. Query UTC half-open `[start, end)` windows; display local time separately.
6. Attribute profiles from persisted invocation/run context. Where missing, show **Unattributed** with any known model/deployment; do not map to today's profile just because deployments match. Add minimal profile/time metadata to future checkpointed results if inspection confirms it is missing.
7. Missing usage is unknown, not zero. Provider-reported consumption is an estimate of observed usage, not invoice reconciliation. Surface coverage/deletion/retention limits established by inspection.
8. Retain historical deployment/profile labels from records. Catalog edits must not relabel recorded usage.
9. Current profile configuration is installation-wide. Do not claim user/tenant isolation the existing schema does not provide.

## PostgreSQL query design

Prefer a normalized SQL read model over repeatedly decoding all checkpoint blobs in every browser request. First establish the existing relational/checkpoint representation and reuse existing decoding helpers where appropriate.

- If usage and identities are directly queryable, use a view/SQL function to normalize them, then bounded aggregate queries for totals, profiles and buckets.
- If decoding Python-serialized checkpoint blobs in SQL is unsuitable, extract unique message usage into a rebuildable, checkpoint-derived projection. This is a reporting index of existing records, not a second authoritative provider ledger. Define incremental position, deduplication and checkpoint deletion behavior before using it.
- A read-only SQL function is reasonable for reusable normalization/aggregation. Stored procedures that periodically collect data are only necessary if a derived projection is demonstrably needed.
- Bound date ranges/profile filters and parameterize values. Keep totals/profile rows/series on one consistent database snapshot.
- Zero-fill intervals with no calls, but keep unknown usage distinct from known zero.
- Validate indexes and query plans against representative stored data before adding materialized views, rollups or refresh scheduling.

Return totals, per-profile/model breakdowns, chronological buckets, call counts, coverage/attribution counts, and estimated cost with currency and pricing basis. Do not report token availability or cost coverage as complete when required dimensions are absent.

## API and surfaces

Proposed `GET /api/ui/insights/token-usage?range=24h|7d|30d&profile=...` returns the standard 202 acknowledgement and emits `insights.token_usage.snapshot` with request correlation and query identity. Payload includes range/start/end, generated time, usage/cost totals, profile rows, time series and coverage/pricing metadata.

Add a dedicated Redux Insights domain and ConfigContext endpoint. Keep state keyed by query or reject stale snapshots so range/profile changes cannot be overwritten by older responses. Panel open, range changes, reconnect and Refresh request the selected view. No independent always-on polling requirement.

The dashboard provides token/cost totals, trends, profile ranking, deployment breakdown and profile filters. The compact panel shows summary usage/cost and links to `/insights`. Reuse selectors and presentation primitives; preserve docking/pinning. Include loading, no activity, unavailable, unattributed, unpriced, partial-coverage and error/retry states. Remove all fake widget calculations and widget-refresh dependencies.

## Implementation sequence and proof

1. **Inspect actual persisted usage.** Execute read-only checkpoint queries and inspect metadata shapes, not message contents. Demonstrate where unique usage/profile/time fields live and how duplicate checkpoints/nested graphs behave. Resolve concrete gaps before choosing direct SQL versus a derived projection.
2. **Implement pricing end to end.** Update profile validation/default population/public projection/PATCH/editor. Verify decimal validation, absent versus zero rates, cache discounts and mixed currencies. Populate only verified or explicitly configured prices.
3. **Implement checkpoint normalization and aggregation.** Prove unique-call totals across repeated checkpoints, tool rounds and nested graphs; correct range boundaries, profile attribution, unknown fields, profile changes/deletion, pricing edits and token/cost aggregate agreement. Add minimal future checkpoint metadata only where needed.
4. **Expose the existing HTTP/SSE contract.** Verify real route/snapshot delivery, stale range/profile responses, reconnect and errors.
5. **Build both Insights surfaces.** Register `/insights` under Spectate. Browser-smoke known usage for two profiles: matching filters yield matching panel/dashboard totals, range/profile filtering works, Refresh incorporates new usage, and unpriced/unattributed/error states are distinct. Exercise profile rate editing and verify explicitly labelled recalculation.
6. **Document observed semantics.** Update system/backend docs with actual source coverage, attribution, current-rate cost estimates, query performance and checkpoint retention/deletion behavior. Keep permanent tests for consumer-visible accounting, pricing and race/boundary invariants—not wiring copies.
