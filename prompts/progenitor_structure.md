# Progenitor Core Improvement Specification

## Scope (CRITICAL)

This build type improves Progenitor itself. All changes MUST be limited to:

- `app/progenitor/`

Do NOT create new agents in `app/agents/` and do NOT create MCP tools in `app/mcp/` unless the specification explicitly calls for it.

## Build Discipline

- Keep diffs minimal and focused
- Preserve existing behavior unless the spec explicitly changes it
- Add/update tests only when behavior changes
- Prefer clear, maintainable code over cleverness

