---
name: Task Setup
description: Prepare a task worktree with explicit setup tools before starting the main build flow.
---

# Task Setup Skill

Before the main build flow starts:

- create or reuse the planned worktree;
- call `prepare_worktree` on the worktree you intend to use;
- stop on blockers like `local_changes` or `non_ff`;
- only proceed to prompt rendering, Copilot build, validation, commit, and PR work after setup reports `ready=true`.
