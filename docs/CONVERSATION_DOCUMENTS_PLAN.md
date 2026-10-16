# Session documents plan

Branch: `feat/conversation-documents` (off `feat/workflows`, carrying the uncommitted
virtual `conversation` trigger work, which this plan deletes).

Goal: one jsonb document per chat conversation, `progenitor.state` / `session:<thread_id>`,
created by Postgres the moment a user message lands in a conversation thread. Workflows
trigger on it, Progenitor reads and edits it as `/sessions/<file>` (front matter + IRC
text), the sidebar follows it live. Clean cutover: `backend.chat.sessions`, its
`__index__`, and the `progenitor.conversations.{meta,logs}` cache are removed; existing
rows are not migrated.

Acceptance example: user sends the first message of a new chat → `session` `created`
event → workflow generates a short title → writes `title` → sidebar shows it without a
reload. Proven by extending `app/frontend/e2e/conversation-title.spec.js`.

## Probe results (live DB, 2026-10-13)

- Messages are not in `checkpoints` or `checkpoint_blobs`; each turn's input lands in
  `checkpoint_writes` (`channel = 'messages'`, msgpack) at the turn's `source = 'input'`
  root checkpoint, written before the model runs.
- A plpgsql decoder that finds the `langchain_core.messages.human` marker and reads the
  msgpack str after the `content` key matched LangGraph's own serde on **180/180** input
  writes (88 human, 92 non-human → NULL). List (multimodal) content takes the first
  `text` part; no such rows exist yet, so that branch is untested.
- Selectivity: 3723 root writes / 1982 root checkpoints → 955 `messages` writes → 88
  human input writes → **72 in Progenitor threads across 64 threads**. Creating the
  document once per thread = 64 `created` events for all of that history.
- `configurable` keys are copied into checkpoint metadata (`model_profile` appears there
  today), so the runtime can tag conversation turns without any new mechanism.
- All documents already live in `store`; no table move.

## Phase 1: Postgres creates the session document

- Installed in `CHECKPOINT_TRIGGER_SQL` (`app/progenitor/routines/events.py`), next to
  the routine checkpoint trigger: runs after `saver.setup()`, when the checkpoint tables
  exist, under the same advisory lock.
- `progenitor_human_text(bytea)`: the decoder above.
- `AFTER INSERT ON checkpoint_writes FOR EACH ROW WHEN (NEW.checkpoint_ns = '' AND
  NEW.channel = 'messages')` → `progenitor_create_session()`:
  - join the checkpoint; continue only if `metadata->>'source' = 'input'` and
    `metadata->>'progenitor_source' IN ('ui', 'telegram', 'api')`;
  - decode the human text; continue only if not NULL;
  - `INSERT INTO store (prefix, key, value) VALUES ('progenitor.state',
    'session:' || thread_id, {payload}) ON CONFLICT DO NOTHING`.
- `ON CONFLICT DO NOTHING` makes it once-only, which also fixes the retried-first-turn
  double fire. `put_writes` upserts, so retries are UPDATEs and never reach the trigger.
- The existing store trigger turns the insert into a `session` `created` document event.
- Runtime: `http_chat` puts `progenitor_source` (`ui|telegram|api`, from the request's
  `metadata.source`) into `configurable`. Workflow steps and routines are not tagged, so
  they get no session (graph runs come later).
- Initial payload: `id`, `thread_id`, `source`, `title: "New chat"`, `first_message`
  (capped at 4000 chars), `pinned: false`, `archived: false`, `created_at`,
  `last_activity`, `content: ""`.

## Phase 2: `session` document type; delete the virtual event

- Built-in type `session` (`key_prefix "session:"`,
  `data/schemas/session.schema.v1.json`, `writable: [title, pinned, archived]`).
  `document_read`/`document_query`/event nodes work on it unchanged.
- The new-chat trigger is an event node on `session`, op `created`.
- Store trigger: an `updated` on a `session:` key whose changed keys are a subset of
  {`content`, `last_activity`} is not logged, so turns don't fire events.
- Delete: `conversations/events.py`, `append_document_event` (store + manager),
  `_publish_conversation_started`, `DocumentType.event_prefix`/`virtual`, the
  `conversation` type and schema, `include_workflow_threads`, the frontend `virtual`
  filtering, and their tests. The `metadata.source` ingress labelling stays (it feeds
  `progenitor_source`).

## Phase 3: backend reads session documents

- `app/backend/chat_sessions.py` lists, reads, pins, archives, renames, and deletes
  `session:` documents. `backend.chat.sessions`, `__index__`, `ui_owned`,
  `_checkpoint_metadata`, and the backend's row creation are removed. Delete = delete the
  document.
- "+ New" is a draft in the frontend only until the first send.
- A backend handler on `session` document events broadcasts the sidebar update, so
  renames by the route, a workflow, or Progenitor all show up live.

## Phase 4: `/sessions/` is the document

- Rename `/conversations/` to `/sessions/`. A file is YAML front matter (document fields
  except `content`) followed by the IRC-style `content`.
- When a turn finishes, the runtime renders the IRC text with the existing
  `transcript.py` and writes `content` and `last_activity` onto the document (phase 2
  keeps this from firing events). Delete `progenitor.conversations.{meta,logs}` and the
  read-time cache in `service.py`.
- Edits change only `writable` front-matter fields; any other change → error naming the
  allowed fields. Writes carry `progenitor.origin` (run id inside a workflow run).
- Front matter shows the stored `title`; the `-!- Topic` line keeps `display_title`.

## Verification

- Permanent test: the SQL decoder vs LangGraph serde over generated messages (str,
  str8/16/32 lengths, unicode, list content), so a LangChain serde change fails loudly.
- Backend/runtime tests for creation once per thread, untagged threads skipped, activity
  writes not logged, and the edit rules.
- Full suites with timeouts; ruff, pyright, eslint.
- Extend `conversation-title.spec.js`: first message → one `session` `created` event
  with `first_message` → workflow writes `title` → the sidebar shows it; a second
  message fires nothing.
- Docs: `managing-workflows` SKILL.md, `data/schemas/README.md`, CHANGELOG.

## Decisions

- D1 Tag: `configurable.progenitor_source` with `ui|telegram|api`; workflow and routine
  threads are untagged and get no session.
- D2 Workflow threads drop out of the sidebar.
- D3 Both write paths exist: the `document_write` workflow node (used by the title
  example) and Progenitor editing `/sessions/` front matter.
- D4 Image-only first message: created with `first_message: "* sends an image"`.
- D5 Delete removes only the session document; the thread's checkpoints stay.
