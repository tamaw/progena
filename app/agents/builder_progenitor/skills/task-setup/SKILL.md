---
name: Task Setup
description: Prepare a task worktree from origin/main before starting the main build flow.
---

# Task Setup Skill

Before the main build flow starts:

- create a fresh task worktree from `origin/main`;
- ensure the planned task branch is active;
- use git tools to confirm the workspace is ready;
- only proceed to prompt rendering, Copilot build, validation, and commit work after setup is complete.
