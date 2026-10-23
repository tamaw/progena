# Claude Code repository guide

Read `AGENTS.md` for setup, validation, current architecture and repository safety.
Also read the guide for each component you change:

- `src/progena/AGENTS.md` — domains, DeepAgent, persistence and runtime
- `src/backend/AGENTS.md` — feature APIs, lifecycle and browser events
- `src/frontend/AGENTS.md` — feature UI, Redux and streaming

Python imports use `src.*`; activate the venv before Python commands. Use the
pinned Pyright and commands in the root guide. `docs/BACKEND.md` owns the browser
acknowledgement/event contract; `docs/STATE_DOCUMENTS.md` owns persistence rules.
Follow the root merge/deployment policy and report actual validation results.
