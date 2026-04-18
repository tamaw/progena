---
name: task-teardown
description: Finish a builder task by pushing committed work, opening a PR, and removing the temporary worktree.
allowed-tools: git gh read_file
---

# Task Teardown Skill

## Teardown Sequence
1. Inspect the prior build results in the current thread.
2. If a commit exists, push the branch and create a pull request.
3. Clean up the task worktree once teardown actions are complete.

## Rules
- Do not claim a PR exists unless `create_pull_request` returned one.
- If no commit was produced, say that plainly and skip PR creation.
- Always call `remove_worktree` before finishing unless cleanup is impossible; if cleanup fails, surface that explicitly.
