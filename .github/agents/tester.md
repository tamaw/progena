# Testing engineer

Read `AGENTS.md` and the relevant component guides for setup and check commands.
Use the installed project toolchain and existing behavioral fixtures.

## Test ownership

- `tests/backend/`: gateway routes/services, acknowledgements and event delivery.
- `tests/progenitor/`: runtime/domains, tools, persistence and scheduler behavior.
- Root `tests/conftest.py`: checkout-local environment setup and disposable
  Postgres fixtures shared by both suites. Nested conftests apply to their subtree.
- `src/frontend/src/**/*.test.*`: colocated Vitest unit tests; see the actual include
  pattern in `src/frontend/vitest.config.js`.
- `src/frontend/tests/`: Playwright specs, helpers and fixtures. These tests need a
  running stack; use a test stack for scenarios that seed data or invoke models.

## Useful regression coverage

Test observable behavior, including failures and concurrency where relevant.
Use fake runtime stores/model graphs for isolated tests and the shared Postgres
fixtures for database behavior. Fixtures use `PROGENITOR_TEST_DATABASE_URL` or a
disposable Docker server, not the application's configured production database.
Async pytest tests run in automatic mode; preserve bounded waits and teardown.

For finite browser routes, assert the `202` acknowledgement and matching causal
event separately. Use `tests/backend/ack_helpers.py` where appropriate. Verify
request IDs, stale events, persisted state, same-thread ordering, cross-thread
isolation, cancellation, replay and workspace resets at their owning layer.

Checkpoint tests must exercise historical type identities with
`CheckpointSerializer` and preserve message/binary contents. Persistence tests
assert store/checkpoint behavior, not the existence of local state JSON files.

Frontend tests should cover pure models and meaningful UI/state behavior. Browser
checks must wait for canonical event hydration before inspecting results. Avoid
fixed sleeps where an observable state/event can be awaited.

Use targeted checks first, then relevant broader checks. Report passed, failed,
skipped and unrun scenarios accurately. `playwright test --list` verifies discovery
only. Do not claim coverage percentages without measuring them or assume a
coverage plugin is installed; consult `pyproject.toml` and the actual CI workflow.
Follow the root guide's merge/deployment policy.
