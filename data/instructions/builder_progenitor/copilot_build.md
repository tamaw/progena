---
title: Copilot build
description: Task handed to Copilot to implement a build inside its worktree.
variables: [branch_name, context_json, copilot_goal, description, planned_at_utc, request_text, skill_context, success_criteria, task_name, validation_targets, work_item_id]
---
Implement the following build task inside the provided worktree.

Task metadata:
- Task name: {task_name}
- Work item id: {work_item_id}
- Branch name: {branch_name}
- Planned at (UTC): {planned_at_utc}
- Description: {description}
- Copilot goal: {copilot_goal}

Success criteria:
{success_criteria}

Validation targets:
{validation_targets}

Original request:
{request_text}

Execution context (JSON):
{context_json}

Repository skills:
{skill_context}

Implementation guidance:
- Make the smallest complete change that satisfies the request.
- Preserve existing patterns unless the task requires a change.
- Leave the worktree in a state that can pass the listed validation targets.
