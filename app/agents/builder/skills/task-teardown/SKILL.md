---
name: Task Teardown
description: Push committed builder work, open the PR, and remove the task worktree.
---

# Task Teardown Skill

When the build phase is done:

- inspect whether the task produced a commit;
- if it did, push the branch and call `create_pull_request`;
- remove the task worktree before finishing;
- report cleanup failures explicitly instead of ignoring them.
