---
name: managing-mcp
description: Add, remove, edit, enable, or disable external MCP servers by editing the /state/mcp.json config file. Use when a user asks to connect, register, update, or drop an MCP server, or to turn one on/off.
allowed-tools: read_file edit_file write_file mcp mcp_refresh
---

# Managing MCP Servers

MCP server configuration lives in a single virtual file: `/state/mcp.json`. It is
backed by the runtime state document (Postgres is the source of truth), so editing
it is the supported way to manage servers — there is no separate "add server" tool.
Every successful write is validated and triggers live tool re-discovery automatically,
so a connected server's tools become available without a restart, and the Tools page
updates live.

## When to Use
- User asks to connect / add / register a new MCP server
- User asks to remove / disconnect a server
- User asks to change a server's URL, headers, or description
- User asks to enable or disable a server

## Always Read Before You Edit
`/state/mcp.json` is serialized with sorted keys and 2-space indentation. Read it
first so your `edit_file` `old_string` matches exactly:

1. `read_file /state/mcp.json`
2. Make the change with `edit_file` (preferred — preserves the rest of the file) or
   `write_file` (only when rewriting the whole document).

## File Schema
```json
{
  "mcpServers": {
    "<server-name>": {
      "url": "https://host/mcp",
      "transport": "http",
      "enabled": true,
      "description": "What this server provides.",
      "headers": { "Authorization": "Bearer ${SOME_TOKEN}" }
    }
  }
}
```

Rules enforced on every write (a violating edit is rejected and nothing changes):
- Top level must be a JSON object containing an `mcpServers` object.
- Each server must be an object with a non-empty `url` (or `endpoint`).
- `transport` must be `http` — remote HTTP servers only. SSE/stdio are not supported.
- `headers` is optional; values may use `$VAR` / `${VAR}` placeholders resolved from
  the runtime environment (use these for secrets — never paste raw tokens).

## Common Operations

### Add a server
Add a new key under `mcpServers`. Minimal:
```json
"github": { "url": "https://api.githubcopilot.com/mcp/", "transport": "http", "enabled": true }
```

### Enable / disable
Flip the `enabled` boolean for that server. Disabling keeps the config; the server's
tools simply stop being discovered.

### Edit URL / headers / description
Change the relevant field in place with `edit_file`.

### Remove a server
Delete its entire key from `mcpServers`.

## After the Change
- The write hook re-discovers tools automatically. If a user reports a server's tools
  did not appear (e.g. the endpoint was briefly down), use `mcp_refresh` to re-poll
  without editing the config again.
- Use `mcp` to show current integration status and the live list of discovered tools
  per server — this reflects what each server actually exposes right now, which the
  config file does not.

## Confirmation Pattern
After a successful edit, reply with one short sentence confirming:
1. What changed (added/removed/enabled/updated) and the server name.
2. Its endpoint.
Only ask a follow-up question if a required detail (the endpoint URL) is missing;
otherwise make the change.

## Troubleshooting
- Edit rejected as invalid: re-read the file and check the schema rules above
  (most often a missing `url` or a non-`http` transport).
- Tools not appearing after add: confirm `enabled: true`, then `mcp_refresh`.
- `edit_file` old_string not found: re-read the file — keys are sorted and indented
  with 2 spaces, so match the on-disk formatting exactly.
