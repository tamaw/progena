# Knowledge feature — refactors, improvements & legacy to remove

Review of the pgvector Knowledge tab (`app/frontend/src/pages/KnowledgePage.jsx`,
`knowledgePageModel.js`, `app/backend/knowledge_routes.py`,
`app/progenitor/knowledge/*`).

**Rating = how worth doing it is, out of 5** (5 = high value).

## Refactors / improvements

### 1. Split `KnowledgePage.jsx` — ★★★★★
It's ~1308 lines with 43 `useState`. One component owns every tab, every form,
and all fetch logic. Extract `OverviewTab` / `SourcesTab` / `ConfigureTab` /
`QueryTab` subcomponents and lift data calls into a `useKnowledgeBases` hook.
Biggest maintainability win.

### 2. Collapse duplicated edit-toggle handlers into a hook — ★★★★☆
Name, description, connection, and embedding each have a near-identical
`start/cancel/save` trio plus `editing*/saving*/draft*/error*` state (≈20 of the
43 `useState`s). A `useEditableField({ load, save })` hook replaces all of them.
Same for the three add-forms (tags / collections / folder).

### 3. De-duplicate backend route boilerplate — ★★★☆☆
`knowledge_routes.py` has ~15 copies of
`def _work(): with psycopg.connect(url)… ; await asyncio.to_thread(_work);
if base is None: 404`. A `_mutate_base(base_id, fn)` helper removes most of it.

### 4. Consider one `PATCH /{id}` instead of 5 setter routes — ★★★☆☆
`/name`, `/description`, `/tags`, `/connection`, `/embedding` are all "update some
columns on a base." A single partial-update endpoint (+ one frontend `patchBase`)
shrinks both sides materially.

### 5. Use a connection pool — ★★★☆☆
Every request does a fresh `psycopg.connect`. A `psycopg_pool.ConnectionPool`
(or async pool) is cheap to add and more robust under load.

## Legacy / fallback / dead code to remove

### 6. Dead `color` machinery — ★★★★☆
`colorForId`, `KB_COLORS`, and the `color` field in `knowledgePageModel.js` are
unused — the card dot now uses a status-tone class and the row icons were removed.
Pure dead code; safe delete.

### 7. Unused `scope` field — ★★★☆☆
Set in `fromApiBase`, never read in the UI (backend also defaults it). Drop it or
surface it.

### 8. `persisted` is always `true` — ★★★☆☆
Since the static-seed fallback was removed, every base is persisted. The
`if (selectedBase.persisted)` guard in `deleteSelected` and the six "This store is
not persisted in Postgres yet" 404 branches are effectively unreachable. Simplify.

### 9. Over-built status machinery — ★★★☆☆
`status` is always `'connected'` (nothing sets syncing/error/idle anymore). So the
**Syncing/Error** filter chips, the syncing/error/idle entries in
`KB_GROUP_ORDER`, and most of `KB_STATUS_META` never fire. Either wire real
statuses or trim to what's reachable.

### 10. Fake "On disk" estimate — ★★★☆☆
`sizeBytes: documents * 4096` is invented. Either compute it for real
(`pg_total_relation_size` / per-base sum) or drop the row (and the `formatBytes`
import). Same spirit as the Sync button that was removed.

### 11. Dead per-collection count branch — ★★☆☆☆
`collection.chunks` is always `0`, so the `chunks > 0 ? "N vectors" : "collection"`
conditional only ever shows "collection". Either populate real counts
(`GROUP BY base_id, collection`) or remove the branch.

### 12. `metric` / `index_type` are stored but ignored — ★★★☆☆
Search is hardcoded to cosine (`<=>`) with no ANN index, so the editable
Metric/Index fields (and the `HNSW`/`IVFFlat` options this pgvector build can't
create) are inert metadata. Either make search honour `metric`, or mark them
clearly read-only/aspirational.

## Bigger picture (not legacy, but the real ceiling)

### 13. Wire a real embedding model behind a flag — ★★★★☆
The `progenitor-hash-256` embedder is lexical-only; it's what makes #12 cosmetic.
Making `embed_text()` call Azure OpenAI when a key is present (hash fallback
otherwise) would make the embedding config *mean* something and is the
highest-value functional improvement.

## Suggested order

- **Quick, safe deletions:** #6 (dead color code), #8 (`persisted` cruft).
- **High-impact structural:** #1, #2.
- **Highest functional value:** #13 (real embeddings), which also resolves #12.
