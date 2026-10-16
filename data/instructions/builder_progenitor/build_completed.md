---
title: Build completed event
description: Lifecycle hook input sent when a build completes.
---
Lifecycle event: build_completed

Task id: {task_id}
Request:
{request_text}

Request context (JSON):
{request_context_json}

Planned build (JSON):
{planned_build_json}

Result text:
{result_text}

Event metadata (JSON):
{metadata_json}

Review the completed build outcome. Use tools only if needed to verify or summarize the completion state, then return a concise note.
