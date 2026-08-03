---
name: managing-marketplaces
description: Register, review, or remove plugin marketplace sources by editing the /config/marketplaces.json config file. Use when a user asks to add, register, review, or remove a marketplace, or before installing a plugin from one that isn't registered yet.
allowed-tools: read_file write_file edit_file structured_output
---

# Managing Marketplaces

Marketplace registrations live in a single virtual file: `/config/marketplaces.json`. It
is backed by the runtime state document (Postgres is the source of truth), so editing it
is the supported way to register a marketplace — there is no separate "add marketplace" tool.
Every successful write is validated and persists immediately; the Settings → Marketplaces
page in the UI updates live.

Registering a marketplace tells Progenitor where to fetch its catalog. Progenitor accepts
Claude-style ``.claude-plugin/marketplace.json`` and GitHub Copilot-style
``.github/plugin/marketplace.json`` manifests. It does not install anything by itself —
use the ``managing-plugins`` skill afterward to install a specific plugin from it.

## When to Use
- User asks to add / register / connect a marketplace
- User asks to review which marketplaces are registered
- User asks to remove / drop a marketplace
- Before installing a plugin whose marketplace isn't registered yet (register it first)

## Always Read Before You Edit
`/config/marketplaces.json` is serialized with sorted keys and 2-space indentation. Read
it first so your `edit_file` `old_string` matches exactly:

1. `read_file /config/marketplaces.json`
2. Make the change with `edit_file` (preferred — preserves the rest of the file) or
   `write_file` (only when rewriting the whole document).

## File Schema
```json
{
  "marketplaces": {
    "acme-tools": {
      "source": "https://github.com/acme/plugins",
      "added_at": "2026-07-29T10:00:00Z"
    }
  }
}
```

Rules enforced on every write (a violating edit is rejected and nothing changes):
- Top level must be a JSON object containing a `marketplaces` object. No other top-level keys.
- Each key is the marketplace name and must be kebab-case (lowercase letters, digits, hyphens).
- Each entry must be an object with a non-empty `source` — a GitHub reference such as
  `owner/repo`, `owner/repo@ref`, or a full `https://github.com/owner/repo` URL.
- `added_at` is optional (an ISO 8601 timestamp); omit it for a new entry, or set it
  yourself if the user cares about recording when it was registered.

## Common Operations

### Register a marketplace
1. `read_file /config/marketplaces.json`
2. `edit_file /config/marketplaces.json` to insert `"<name>": {"source": "<owner/repo>"}` under
   `marketplaces`, keeping every other entry untouched.

### Remove a marketplace
Delete its entire key from `marketplaces`. This does not uninstall plugins already
installed from it — use `managing-plugins` to remove those separately if the user wants that.

### Review registered marketplaces
`read_file /config/marketplaces.json` and summarize the `marketplaces` keys and their sources.

## Confirmation Pattern
After a successful edit, reply with one short sentence confirming what changed
(registered/removed) and the marketplace name. Only ask a follow-up question if the
source is missing or ambiguous; otherwise make the change.

## Troubleshooting
- Edit rejected as invalid: re-read the file and check the schema rules above
  (most often a non-kebab-case name or a missing `source`).
- `edit_file` old_string not found: re-read the file — keys are sorted and indented
  with 2 spaces, so match the on-disk formatting exactly.
- Installing a plugin fails with "marketplace not found": the marketplace referenced
  isn't registered yet — register it here first.
