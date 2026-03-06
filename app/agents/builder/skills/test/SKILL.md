---
name: test
description: Run focused test commands for the active worktree. Use after building and before lint or PR creation unless the user explicitly skips testing.
allowed-tools: run_worktree_command execute
---

# Test

- Run the smallest relevant test command first.
- Capture failures clearly and return to build if needed.
