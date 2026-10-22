# Progenitor — Copilot instructions

Read `AGENTS.md` for shared commands, current source layout, persistence/event
contracts and repository safety. Read the `AGENTS.md` under each affected component
before editing it. Keep this entry point short; shared instructions belong in the
root guide and component-specific instructions belong with their code.

- Python imports and entry points use `src.backend.*` and `src.progenitor.*`.
  Activate the project venv before Python commands. Use pinned Pyright with the
  explicit venv interpreter, matching CI; do not substitute mypy.
- Organize behavior in feature/domain packages. Keep contracts independently
  importable and keep runtime/application assembly out of shared infrastructure.
- Persist state, mutable configuration and checkpoints in Postgres. Do not
  recreate local `current_state.json` / `build_state.json` persistence.
- Preserve per-thread isolation, checkpoint compatibility and the browser's
  request-correlated HTTP acknowledgement/SSE contract in `docs/BACKEND.md`.
- Frontend state and pages live under `src/frontend/src/features/`; shared UI,
  infrastructure and application composition remain separate. Use the frontend
  guide for Redux and event ownership.
- Python tests live under `tests/backend/` and `tests/progenitor/`. Frontend unit
  tests are colocated; browser tests live under `src/frontend/tests/`. Use existing
  test doubles and disposable databases rather than production services.
- Do not merge PRs or trigger deployments without an explicit human request.
  Cancel an accidentally started deployment-capable workflow and notify the human.
