---
name: managing-plugins
description: Install, pin, update, or remove marketplace plugins by editing the /config/plugins.json config file. Use when a user asks to add, install, update, pin, or drop a plugin, or to review what plugins are installed.
allowed-tools: read_file write_file edit_file structured_output
---

# Managing Plugins

Plugin configuration lives in a single virtual file: `/config/plugins.json`. It is
backed by the runtime state document (Postgres is the source of truth), so editing
it is the supported way to manage plugins — there is no separate "install plugin" tool.
Every successful write is validated and triggers live skill installation automatically,
but the newly installed skills only become active in a **new** conversation, because
the skills inventory is loaded once per session. The agent does not need to do anything
else after writing the file; the reconcile runs automatically and promptly.

## When to Use
- User asks to install / add / connect a plugin from a marketplace
- User asks to update / pin a plugin to a specific version
- User asks to remove / drop / uninstall a plugin
- User asks to review what plugins are currently installed

## Always Read Before You Edit
`/config/plugins.json` is serialized with sorted keys and 2-space indentation. Read it
first so your `edit_file` `old_string` matches exactly:

1. `read_file /config/plugins.json`
2. Make the change with `edit_file` (preferred — preserves the rest of the file) or
   `write_file` (only when rewriting the whole document).

## File Schema
```json
{
  "plugins": {
    "acme-tools/formatter": {
      "source": {"source": "github", "repo": "acme/formatter-plugin", "ref": "v2.1.0"},
      "version": "2.1.0"
    }
  }
}
```

Rules enforced on every write (a violating edit is rejected and nothing changes):
- Top level must be a JSON object containing a `plugins` object. No other top-level keys.
- Each plugin must be keyed by `<marketplace>/<plugin>`, e.g. `acme-tools/formatter`.
- Each entry must be an object with a non-empty `source` — a relative path string or an
  object such as `{"source": "github", "repo": "...", "ref": "..."}`.

The agent only ever writes two fields: `source` (required) and `version` (optional pin).
All other fields are **runtime-owned** and must never be hand-written:
`installed_version`, `installed_digest`, `available_version`, `status`, `status_message`,
`skills`, `installed_at`, `last_checked`. The reconciler fills them automatically.

`available_version` being non-null means an update exists upstream. It is **never**
auto-applied. To apply it, set `version` to the `available_version` value. Removing a
plugin (deleting its key) removes its skills on the next reconcile, but the current
conversation's already-loaded skill list is unaffected.

## Common Operations

### Install a plugin
1. `read_file /config/plugins.json`
2. `read_file /schemas/plugin_entry.schema.v1.json`
3. `structured_output json_schema=<content from step 2> instruction="Build a minimal plugin entry for '<marketplace>/<plugin>' sourced from GitHub repo '<repo>' at ref '<ref>'."`
4. `edit_file /config/mcp.json` to insert `"<marketplace>/<plugin>": <result from step 3>` under
   `plugins`, keeping every other entry untouched. Minimal shape:
```json
"acme-tools/formatter": {
  "source": {"source": "github", "repo": "acme/formatter-plugin", "ref": "v2.1.0"}
}
```
Only bother with `structured_output` when several fields are involved — for a simple
GitHub source, hand-authoring the object is fine.

### Update or pin a version
Set `version` on the entry to the desired pin, or to the value shown in
`available_version` to apply a pending update. If `version` is omitted and
`installed_version` is present, the plugin is frozen at that version.

### Remove a plugin
Delete its entire key from `plugins`.

## After the Change
- The reconcile runs automatically within a second or so. There is no extra tool to call.
- Tell the user the plugin's skills will be active starting in their **next new**
  conversation, not the current one.
- If a user reports a plugin did not appear, re-read `/config/plugins.json` and check
  `status` and `status_message` — a failing plugin lands `status: "error"` with a
  one-line message (e.g. bad source, missing skills, network failure) while other
  plugins continue to reconcile normally.

## Confirmation Pattern
After a successful edit, reply with one short sentence confirming:
1. What changed (installed/updated/removed) and the plugin name.
2. The marketplace it came from, and the version if relevant.
Only ask a follow-up question if a required detail (the source or version) is missing;
otherwise make the change.

## Troubleshooting
- Edit rejected as invalid: re-read the file and check the schema rules above
  (most often a malformed key or a missing `source`).
- Plugin shows `status: "error"`: read `/config/plugins.json` for `status_message`;
  fix the source or network issue, then edit the file again.
- `edit_file` old_string not found: re-read the file — keys are sorted and indented
  with 2 spaces, so match the on-disk formatting exactly.
