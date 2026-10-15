---
title: Build cancelled event
description: Lifecycle hook input sent when a build is cancelled.
variables: [error, metadata_json, planned_build_json, request_context_json, request_text, task_id]
---
Lifecycle event: build_cancelled

Task id: {task_id}
Request:
{request_text}

Request context (JSON):
{request_context_json}

Planned build (JSON):
{planned_build_json}

Error:
{error}

Event metadata (JSON):
{metadata_json}

Review the cancellation context and return a concise note about what was interrupted.
