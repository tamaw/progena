---
name: managing-mcp
description: Add, remove, edit, enable, or disable external MCP servers by editing /config/mcp.json, and enable or disable individual MCP tools via /config/mcp_tools.json. Use when a user asks to connect, register, update, or drop an MCP server, turn one on/off, or turn a single MCP tool on/off.
allowed-tools: read_file edit_file write_file structured_output mcp mcp_refresh
---

# Managing MCP Servers

MCP server configuration lives in a single virtual file: `/config/mcp.json`. It is
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
- User asks to enable or disable one MCP tool while keeping its server on

## Always Read Before You Edit
`/config/mcp.json` is serialized with sorted keys and 2-space indentation. Read it
first so your `edit_file` `old_string` matches exactly:

1. `read_file /config/mcp.json`
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
1. `read_file /config/mcp.json`
2. `read_file /schemas/mcp_server_entry.schema.v1.json`
3. `structured_output json_schema=<content from step 2> instruction="Build an MCP server entry for '<name>' at '<url>' with transport http."`
4. `edit_file /config/mcp.json` to insert `"<name>": <result from step 3>` under
   `mcpServers`, keeping every other server untouched. Minimal shape:
```json
"github": { "url": "https://api.githubcopilot.com/mcp/", "transport": "http", "enabled": true }
```
Only bother with `structured_output` when several fields are involved (e.g. headers
with templated secrets) — for a one- or two-field server, hand-authoring the object is
fine.

### Enable / disable a server
Flip the `enabled` boolean for that server. Disabling keeps the config; the server's
tools simply stop being discovered.

### Enable / disable a single tool
When the user wants one tool off but the rest of its server on, do NOT touch the
server in `/config/mcp.json` (that turns off every tool on it), and do NOT change its
URL. Per-tool on/off lives in `/config/mcp_tools.json`: a `tools` array produced by
MCP discovery, one entry per tool, shaped by `/schemas/mcp_tool_entry.schema.v1.json`
(`name`, `server`, `original_name`, `description`, `enabled`). The only thing you may
change is a tool's `enabled` flag; there is no other setting or override key in that
file.

1. `grep <tool name> /config/mcp_tools.json` (or `read_file` it) to find the tool's
   entry. Names are prefixed with the server: `jina_search_jina_blog`, not
   `search_jina_blog`.
2. `read_file /schemas/mcp_tool_entry.schema.v1.json`
3. `structured_output json_schema=<content from step 2> input=<that tool's current entry>
   instruction="Return this exact entry with enabled set to false."` (or `true`). This
   validates the entry against the schema before you write it.
4. `edit_file /config/mcp_tools.json`, replacing that one entry with the result from
   step 3. Every write is validated against the same schema; adding keys, adding or
   removing tools, or changing any field other than `enabled` is rejected.
5. Verify: call `mcp` and confirm `disabled_count` changed and the tool is no longer
   (or is again) in the live tool list. Only then tell the user it's done.

The flag is the same one the Tools page toggles: the tool stays listed there, can be
switched back on, survives re-discovery, and takes effect on the next turn.

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
