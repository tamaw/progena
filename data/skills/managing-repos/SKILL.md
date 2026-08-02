---
name: managing-repos
description: Declare, edit, or remove tracked repositories by editing the /config/repos.json config file, and check their live git status via the read-only /config/repo_scan.json. Use when a user asks to add/track/drop a repo, or asks for a repo's branch, dirty state, or recent commits.
allowed-tools: read_file write_file edit_file structured_output
---

# Managing Repos

Declared repositories live in a single virtual file: `/config/repos.json` — shaped
`{ "repos": { "<repo-id>": { ... } } }` (mirrors `/config/mcp.json`'s `mcpServers`
map). It is backed by the runtime `repos` state document (Postgres is the source of
truth), so editing it is the supported way to declare/edit/remove a repo — there is
no separate "add repo" tool. Every successful write is validated and the
Repositories page updates live.

**Declaring a repo never clones it.** This file only records intent (name, path,
access). If `local_path` doesn't exist on disk yet, the repo shows as "not yet
cloned" until a human (or a separate git-capable agent) actually clones it there —
this skill does not run `git clone`.

## Live git status is a separate, read-only file
Branch, dirty count, commit/branch counts, and recent commits are **not** in
`repos.json` — they're scanned from disk by the heartbeat (every
`repo_scan_seconds`, default 120s) into `/config/repo_scan.json`
(`{ "scanned_at": ..., "repos": { "<repo-id>": { ... } }, "workspace_signal": {...} }`).
`read_file` that file to answer "what's the status of X" — never guess, and never
try to `write_file`/`edit_file` it (rejected: heartbeat-owned).

## Always Read Before You Edit
`/config/repos.json` is serialized with sorted keys and 2-space indentation. Read it
first so your `edit_file` `old_string` matches exactly:

1. `read_file /config/repos.json`
2. Make the change with `edit_file` (preferred — touches only the one repo you're
   adding/changing/removing) or `write_file` (only when the file is empty, i.e.
   adding the very first repo).

## File Schema
```json
{
  "repos": {
    "my-service": {
      "name": "my-service",
      "local_path": "/home/user/code/my-service",
      "access": "read",
      "remote_url": "github.com/org/my-service",
      "summary": "What this repo is."
    }
  }
}
```

Rules enforced on every write (a violating edit is rejected and nothing changes):
- Top level must be a JSON object containing only a `repos` key.
- Each key (the repo id) must be kebab-case (lowercase letters, digits, hyphens).
- Each entry must be an object with a non-empty `name` and `local_path`, and
  `access` must be exactly `"read"` or `"write"` (declared intent, not
  filesystem-enforced — signals whether the agent may commit/push there).
- `remote_url`, `summary`, `owner` are optional strings.
- `created_at`/`updated_at` are runtime-stamped — never set them yourself; on an
  edit, preserve the existing `created_at` and let the write hook refresh
  `updated_at`.

## Golden rules
1. **Read before you write.** `read_file /config/repos.json` first.
2. **Preserve everything you're not changing** — every other repo verbatim.
3. **Repos are keyed by a kebab-case id you derive from the name** (no generator
   tool for this one) — lowercase, spaces/underscores → hyphens, strip anything
   else non-alphanumeric. `"My Service!"` → `"my-service"`.
4. **`local_path` may be relative** — a relative path resolves against the managed
   repos directory (`data/repos` locally), so a repo meant to live there just needs
   its folder name (e.g. `"my-service"`), not a full path.
5. **Never regenerate the whole file.** Build only the one repo object with
   `structured_output` (for several fields) or hand-author it directly (one or two
   fields) and splice it into `repos` with `edit_file`.

## Common Operations

### Declare / add a repo
1. `read_file /config/repos.json`
2. Derive the kebab-case id from the name.
3. For a couple of fields, hand-author the object directly with `edit_file`:
```json
"my-service": { "name": "my-service", "local_path": "/home/user/code/my-service", "access": "read" }
```
   For several fields (remote, summary, owner), `read_file /schemas/repo_entry.schema.v1.json`
   and run `structured_output json_schema=<that content> instruction="Build a repo entry named '<name>' at local_path '<path>' with access '<read|write>'."`,
   then `edit_file` the result in under `repos` with the derived id as the key.

### Edit a field (e.g. change access or local_path)
Change the relevant field on that repo in place with `edit_file`, preserving
`created_at`.

### Remove a repo
Delete its entire `"<id>": { ... }` key from `repos` with `edit_file`.

### Check a repo's live status
`read_file /config/repo_scan.json`, look up the repo's id under `repos`. Key
fields: `branch`, `dirty_count`, `commit_count`, `branch_count`, `commits_30d`,
`last_commit`, `error` (non-null means the path isn't a git repo, or doesn't exist
at all — `exists: false`). This can lag the real repo by up to
`repo_scan_seconds` (heartbeat cadence), so say "as of the last scan" rather than
implying it's live-live.

## Confirmation Pattern
After a successful `repos.json` edit, reply with one short sentence confirming
what changed (declared/updated/removed) and the repo's name — and if you declared
a new one, note it won't show real git data on the Repositories page until it's
actually cloned to `local_path` and the next heartbeat scan runs. Only ask a
follow-up question if a required detail (the path) is missing; otherwise make the
change.

## Troubleshooting
- Write rejected as invalid: re-read the file and check the rules above (most
  often a missing `local_path`, an `access` value that isn't `read`/`write`, or a
  non-kebab-case id).
- `edit_file` old_string not found: re-read the file — keys are sorted and
  indented with 2 spaces, so match the on-disk formatting exactly.
- Repo shows "not yet cloned" / `exists: false` in `repo_scan.json`: `local_path`
  doesn't resolve to a directory on disk yet — this skill never clones; someone
  needs to `git clone` it there first.
- Status looks stale: `repo_scan.json`'s `scanned_at` shows when it last ran;
  it only updates every `repo_scan_seconds`, not on every request.
