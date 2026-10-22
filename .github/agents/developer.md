# Feature developer

Read `AGENTS.md` and the affected component guides before implementing a change.
Use their setup/check commands and the existing tests as the behavioral contract.
Python supports the version range in `pyproject.toml`; frontend versions and
scripts are defined in `src/frontend/package.json`.

## Implementation

- Put runtime domain contracts, validation and stores under their owning
  `src/progenitor/` domain. Keep DeepAgent composition in `agent/`, graph assembly
  in `orchestration/` and process/transport behavior in `runtime/`.
- Put API endpoints in `src/backend/<feature>/routes.py` and behavior in that
  feature's service/store. Register routers in `http/router.py` or `main.py`.
  Follow `docs/BACKEND.md` for acknowledgement bodies, request IDs and causal events.
- Put product UI/state under `src/frontend/src/features/<feature>/`; reusable UI
  belongs in `components/`. Keep feature imports out of shared infrastructure and
  store assembly out of feature code. Preserve lazy route boundaries.
- Use Pydantic v2 for domain models and typed partial updates for graph nodes.
  Keep async I/O non-blocking; preserve cancellation and per-thread isolation.
- Use domain stores/StateManager for persistence. Agent-facing JSON files are
  virtual views of Postgres documents. Keep historical checkpoint model aliases
  and use `CheckpointSerializer` for checkpoint reads.

Add regression coverage for behavior changes, including useful failure cases.
Reuse injected fake model/store boundaries and existing fixtures. Run the affected
checks first and relevant broader checks from the root guide. Inspect UI changes
in a browser, including the affected interaction and asynchronous event updates.

Inspect database-backed state through the existing services; do not diagnose the
current app by looking for local state JSON files. Avoid logging credentials or
private message content. Keep generated artifacts out of commits.

Create a reviewable PR with the problem, behavior and actual validation results.
Follow the root guide's merge/deployment policy.
