# Progenitor — Copilot instructions

Read root `AGENTS.md` and the guide under each affected component before editing.
They own setup, validation, architecture boundaries and repository safety.

Python imports use `src.*`; activate the project venv and use pinned Pyright with
its explicit interpreter. Preserve Postgres/checkpoint compatibility and the
browser acknowledgement/SSE contract in `docs/BACKEND.md`. Follow the root
merge/deployment policy and report passed, skipped and unrun checks accurately.
