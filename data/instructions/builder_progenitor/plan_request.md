---
title: Plan request
description: User request and execution context for the structured planning pass.
variables: [context_json, current_datetime_utc, request_text]
---
User request:
{request_text}

Execution context (JSON):
{context_json}

Current UTC datetime:
{current_datetime_utc}

Extract structured metadata for this build so downstream execution, hooks, logs, and Copilot ACP prompting can use the same identifiers and task description.
