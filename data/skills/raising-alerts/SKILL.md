---
name: raising-alerts
description: Raise a durable alert for the user by creating an /alerts/<id>.json document, or build a workflow that creates alert documents. Use for important status, warnings, failures, or a response that needs user attention.
allowed-tools: read_file write_file edit_file generate_id structured_output
---

Use an alert when the user needs timely attention: an important completion, warning, failure, or decision. Do not use one for routine conversational replies.

## Create an alert

Write one JSON file at `/alerts/<id>.json`; generate a unique `<id>` first. Supply only the user-authored fields:

```json
{"title":"Build failed","message":"Tests failed in CI.","type":"error","urgent":true,"requires_response":false}
```

`title` and `message` are required. `type` defaults to `info`; `urgent`, `requires_response`, and `viewed` default to `false`. Set `urgent` for immediate attention. Set `requires_response` only when the user must answer.

The backend stamps the id from the filename, real creation timestamp, unread state, and source, validates the alert, and relays every newly-created alert document to the user. Do not supply server-managed `id`, `timestamp`, `viewed`, `viewed_at`, `source`, or `context` fields.

## Workflows

A workflow raises an alert with a `document_create` node whose `document_type` is `alert`. Feed dynamic message content through a `format` node and connect its `text` output to the create node's `message` input; connect a title value to `title`. The alert schema defaults mean only title and message are required.
