---
name: scheduling
description: Create, manage, and troubleshoot scheduled tasks, including one-off and recurring jobs with natural language time parsing. Use when users ask to schedule work, review upcoming jobs, or debug task timing.
allowed-tools: schedule_task list_schedule current_time shell
---

# Scheduling Skill

## When to Use
- User asks to schedule a task for later
- User asks to set up recurring tasks
- User wants to review or manage existing scheduled tasks
- Automated task dispatch needs configuration

## Creating Scheduled Tasks
Use the `schedule_task` tool with a natural language time expression:

Examples:
- "in 30 minutes" — one-off task
- "every day at 9am" — recurring daily
- "every monday at 10:00" — weekly recurring
- "tomorrow at 3pm" — one-off future task
- "every 2 hours" — high-frequency recurring

## Schedule Entry Format
Each scheduled task has:
- `id`: Unique identifier
- `description`: What to do when triggered
- `schedule_expression`: Natural language time
- `next_run`: ISO timestamp of next execution
- `recurring`: Boolean
- `enabled`: Boolean

## Managing Tasks
- **List**: Use `list_schedule` to see all tasks
- **Disable**: Tasks can be disabled without deletion
- **Review**: Check `schedule.json` for the full schedule state

## Confirmation Pattern
After scheduling, always confirm:
1. What was scheduled
2. When it will next run
3. Whether it's recurring or one-off

## Troubleshooting
- If a task didn't fire: check `next_run` vs current time
- If time parsing fails: use explicit ISO format as fallback
- Schedule state persists in `data/schedule.json`
