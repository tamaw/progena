---
name: managing-a2a
description: Register, edit, enable/disable, or remove A2A agents by editing the /config/agents.json config file. Use when a user asks to add, update, drop, or toggle a remote agent, or to review what's registered.
allowed-tools: read_file write_file edit_file structured_output list_agents
---

# Managing A2A Agents

Registered agents live in a single virtual file: `/config/agents.json` — shaped
`{ "agents": [...], "tools": [...] }`. It is backed by the runtime `registry` state
document (Postgres is the source of truth), so editing it is the supported way to
manage agents — there is no separate "add agent" tool. Every successful write is
validated, and the Agents page updates live.

## Always Read Before You Edit
Read it first so your `edit_file` `old_string` matches exactly:

1. `read_file /config/agents.json`
2. Make the change with `edit_file` (preferred — preserves the rest of the file) or
   `write_file` (only when rewriting the whole document).

## File Schema
```json
{
  "agents": [
    {
      "name": "my-agent",
      "kind": "a2a_agent",
      "transport": "http",
      "url": "https://host/a2a",
      "description": "What this agent does.",
      "enabled": true
    }
  ],
  "tools": [ ... ]
}
```

Rules enforced on every write (a violating edit is rejected and nothing changes):
- Top level must be a JSON object with only `agents` and `tools` keys.
- `agents` must be an array; every entry must be an object with a non-empty `name`.
- `tools` must be an array — it is runtime-managed (MCP-derived); **never edit it**,
  always preserve it verbatim.

Each agent is keyed by its unique `name` (no generated id needed). Common fields:
`name`, `kind` (e.g. `a2a_agent`), `description`, `transport` (`http`), `url`,
`agent_card_url` (defaults to `<url>/.well-known/agent-card.json`), `enabled`,
`capabilities`. Runtime-owned (never invent, always preserve): `status`,
`status_message`, `last_heartbeat`, `last_error`, `pid`, `created_at`, `updated_at`.

## Golden rules
1. **Read before you write.** `read_file /config/agents.json` first.
2. **Preserve everything you're not changing** — all other agents verbatim, the entire
   `tools` array verbatim, and runtime status fields on the edited agent.
3. **Agents are keyed by `name`** — adding an agent with an existing name updates it.
4. **Scope is A2A agents.** Local ACP/CLI agents involve process setup; use the dedicated
   `register_acp_agent` tool for those, not this skill.
5. **Never regenerate the whole file.** `tools` is runtime-managed and must stay
   byte-for-byte what it was — build only the one agent object with
   `structured_output` and splice it into `agents` with `edit_file`. Don't ask
   `structured_output` to reproduce the whole `{agents, tools}` document.

## Common Operations

### Register / add an A2A agent
1. `read_file /config/agents.json`
2. `read_file /schemas/agent_entry.schema.v1.json`
3. `structured_output json_schema=<content from step 2> instruction="Build an agent entry named '<name>' of kind 'a2a_agent' at url '<url>', enabled true, with a short description."`
4. `edit_file /config/agents.json` to insert the object from step 3 into `agents`
   (or `write_file` only if the array is currently empty). Keep every existing agent
   and the entire `tools` array untouched.

For a one- or two-field registration, hand-authoring the object directly with
`edit_file` is fine — reach for `structured_output` when several fields are involved.

### Edit an agent (e.g. change URL or description)
Change the relevant field on that agent in place with `edit_file`.

### Enable / disable
Flip the `enabled` boolean for that agent.

### Remove an agent
Delete its entire object from `agents`.

## After the Change
For live status (is it actually running?), use `list_agents` — registration only
records the agent in the file; starting/monitoring is a separate runtime concern this
file doesn't track.

## Confirmation Pattern
After a successful edit, reply with one short sentence confirming what changed
(added/updated/enabled/removed) and the agent's name. Only ask a follow-up question if
a required detail (the URL) is missing; otherwise make the change.

## Troubleshooting
- Write rejected as invalid: re-read the file and check the schema rules above (most
  often a missing `name`, or an extra/renamed top-level key).
- `edit_file` old_string not found: re-read the file — match the on-disk formatting
  exactly.
