# Object-agent command shims

`bin/git` and `bin/gh` allow a small read-only command surface for object-agent
shells. They reject destructive subcommands by default and run before the real
system tools on the agent shell's `PATH`.

These scripts are a guardrail, not a security boundary. DeepAgents invokes a
real shell, so an agent can bypass `PATH` with an absolute executable path.
Production containment comes from the dedicated non-root container user,
read-only root filesystem, minimal mounts, dropped capabilities, and network
policy. The image owns the shims as root and makes them non-writable by the
agent user.
