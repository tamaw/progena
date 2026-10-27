---
name: managing-repos
description: Declare, edit, or remove tracked repositories by writing /config/repos/<id>.json files, and check their live git status via the read-only /config/repo_scan.json. Use when a user asks to add/track/drop a repo, or asks for a repo's branch, dirty state, or recent commits.
allowed-tools: read_file write_file edit_file ls structured_output
---

# Managing Repos

Each declared repository is one virtual file, `/config/repos/<repo-id>.json`, backed by a
`repo:<repo-id>` document (Postgres is the source of truth). Writing these files is
the supported way to declare/edit/remove a repo — there is no separate "add repo"
tool. Every write is validated and the Repositories page updates live; each file is
its own `repo` document, so workflows can trigger on a repo being created, updated
or deleted.

**Declaring a repo never clones it.** A file only records intent (name, path,
access). If `local_path` doesn't exist on disk yet, the repo shows as "not yet
cloned" until it is cloned there — this skill does not run `git clone`. The
bundled `repo-manager` agent does the git work (clone, per-branch worktrees,
push and pull requests), e.g. from a routine `agent` step. To post a pull
request comment or a comment-only review, delegate to `repo-manager` with the
worktree, pull request and comment text; it runs `gh pr comment` or
`gh pr review --comment` under its `git_write` + `git_publish` opt-ins.
Approving or requesting changes on a review, merging and `gh api` stay refused.
A relative `local_path` (e.g. `"progena"`) is the usual choice: it lives in the managed
repos directory that the runtime, backend and Claude containers share.

## Live git status is a separate, read-only file
Branch, dirty count, commit/branch counts, and recent commits are **not** in the
repo files — they're scanned from disk by the heartbeat (every
`repo_scan_seconds`, default 1h) into `/config/repo_scan.json`
(`{ "scanned_at": ..., "repos": { "<repo-id>": { ... } }, "workspace_signal": {...} }`).
`read_file` that file to answer "what's the status of X" — never guess, and never
try to `write_file`/`edit_file` it (rejected: heartbeat-owned).

## File Schema
`/config/repos/my-service.json`:
```json
{
  "access": "read",
  "local_path": "/home/user/code/my-service",
  "name": "my-service",
  "remote_url": "github.com/org/my-service",
  "summary": "What this repo is."
}
```

Rules enforced on every write (a violating write is rejected and nothing changes):
- The file name (the repo id) must be kebab-case (lowercase letters, digits,
  hyphens) and end in `.json`.
- The content must be one object with a non-empty `name` and `local_path`, and
  `access` must be exactly `"read"` or `"write"` (declared intent, not
  filesystem-enforced — signals whether the agent may commit/push there).
- `remote_url`, `summary`, `owner` are optional strings.
- `created_at`/`updated_at` are runtime-stamped — never set them yourself; an edit
  keeps the stored `created_at` and refreshes `updated_at`.

## Golden rules
1. **`ls /config/repos/` to see what's declared**, and `read_file /config/repos/<id>.json` before
   editing one.
2. **Repos are keyed by a kebab-case id you derive from the name** (no generator
   tool for this one) — lowercase, spaces/underscores → hyphens, strip anything
   else non-alphanumeric. `"My Service!"` → `"my-service"`. Check `ls /config/repos/` so
   you don't overwrite an existing repo with the same id.
3. **`local_path` may be relative** — a relative path resolves against the managed
   repos directory (`data/repos` locally), so a repo meant to live there just needs
   its folder name (e.g. `"my-service"`), not a full path.
4. **Always build new/replaced entries with `structured_output` against
   `/schemas/repo_entry.schema.v1.json`** — never hand-author the JSON object
   yourself. The schema's `additionalProperties: false` and `required` list are
   exactly the rules the write validates against, so a schema-shaped result can't
   be rejected for a missing/misnamed field the way freehand JSON can.

## Common Operations

### Declare / add a repo
1. `ls /config/repos/` and derive a kebab-case id from the name that isn't taken.
2. `read_file /schemas/repo_entry.schema.v1.json`
3. `structured_output json_schema=<content from step 2> instruction="Build a repo entry named '<name>' at local_path '<path>' with access '<read|write>'<, remote_url '<url>' if given><, summary '<summary>' if given>."`
4. `write_file /config/repos/<id>.json` with the result from step 3.

### Edit a field (e.g. change access or local_path)
`read_file /config/repos/<id>.json`, then `edit_file` the field in place.

### Remove a repo
Delete `/config/repos/<id>.json`.

### Check a repo's live status
`read_file /config/repo_scan.json`, look up the repo's id under `repos`. Key
fields: `branch`, `dirty_count`, `commit_count`, `branch_count`, `commits_30d`,
`last_commit`, `error` (non-null means the path isn't a git repo, or doesn't exist
at all — `exists: false`). This can lag the real repo by up to
`repo_scan_seconds` (heartbeat cadence), so say "as of the last scan" rather than
implying it's live-live.

## Confirmation Pattern
After a successful write, reply with one short sentence confirming what changed
(declared/updated/removed) and the repo's name — and if you declared a new one,
note it won't show real git data on the Repositories page until it's actually
cloned to `local_path` and the next heartbeat scan runs. Only ask a follow-up
question if a required detail (the path) is missing; otherwise make the change.

## Troubleshooting
- Write rejected as invalid: check the rules above (most often a missing
  `local_path`, an `access` value that isn't `read`/`write`, or a
  non-kebab-case file name).
- `edit_file` old_string not found: re-read the file — keys are sorted and
  indented with 2 spaces, so match its formatting exactly.
- Repo shows "not yet cloned" / `exists: false` in `repo_scan.json`: `local_path`
  doesn't resolve to a directory on disk yet — this skill never clones; someone
  needs to `git clone` it there first.
- Status looks stale: `repo_scan.json`'s `scanned_at` shows when it last ran;
  it only updates every `repo_scan_seconds`, not on every request.
