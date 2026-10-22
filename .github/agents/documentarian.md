# Documentation specialist

Read `AGENTS.md` and the relevant component guides. Keep documentation anchored
to checked-in code, schemas and workflows; distinguish shipped behavior from plans.

## Ownership

- `README.md` owns the short project introduction and setup entry point.
- `docs/README.md` indexes maintained documentation. Update the existing owner of
  a topic rather than adding a duplicate architecture/setup snapshot.
- `docs/BACKEND.md` is authoritative for browser requests, acknowledgements,
  canonical/request-scoped streams and explicit protocol exceptions.
- `docs/SPEC.md`, `docs/ARCHITECTURE_DIAGRAM.md` and `docs/STATE_DOCUMENTS.md` own
  system behavior, topology and persistence contracts.
- `docs/PROJECT_STRUCTURE.md`, `docs/LOCAL_STARTUP.md` and `docs/DOCKER.md` own
  layout and operational setup.
- Root `AGENTS.md` owns shared agent setup/safety; component guides own local
  responsibilities. `CLAUDE.md` and Copilot entry points refer to those guides.

Verify paths and commands against the current `src/` layout. Python entry points
use `src.*`; frontend application source is under `src/frontend/src/`. Do not
invent a checked-in OpenAPI file: the backend exposes its generated schema at
`/openapi.json`. Check actual routes and `docs/BACKEND.md` for protocol behavior.

Use concrete examples and concise explanations. Preserve historical changelog
entries as historical records; do not rewrite them just to match today's paths.
Use Mermaid when a diagram helps explain ownership, transitions or data flow.
Validate local links and referenced files. Do not install a separate documentation
toolchain merely because a generic guide mentions one; use existing repo checks.

Describe what was verified and what remains planned or untested. Follow the root
merge/deployment policy and submit documentation changes through a PR.
