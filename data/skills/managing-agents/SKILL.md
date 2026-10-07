---
name: managing-agents
description: Create, inspect, and update local object agents by writing their SYSTEM.md manifests under /agents/user/. Use when asked to create an agent for chat or a routine, or change an existing user agent's instructions, tools, skills, model, or filesystem access. Remote A2A registrations use managing-a2a instead.
allowed-tools: read_file write_file edit_file ls structured_output json_to_yaml
---

# Managing Agents

User agents are virtual files: `/agents/user/<name>/SYSTEM.md`. Each file has
YAML front matter and a Markdown system prompt after the closing `---`.
Writes persist an `agent:user/<name>/SYSTEM.md` document in Postgres, notify
the backend, and update the Agents page. A successful write is sufficient;
do not save again or restart the service to register the agent.

`SYSTEM.md` is the actual filename in this application, including when a user
calls it an agent.md. Native agents under `/agents/native/` (also exposed at
`/agents/objects/`) and installed plugin agents are read-only here.
Remote A2A registrations belong to `/config/agents.json`; use `managing-a2a`.
Routine graphs belong to `/routines/`; use `managing-routines`.

## Create an agent

1. Inspect `/agents/` and `/agents/user/` for existing names. When creating a
   NEW agent, use a new name; page selection is context, not an instruction to
   overwrite the selected agent or its routine.
2. Read `/schemas/agent-manifest.schema.v1.json`. This schema describes the
   YAML front matter. Required fields are `name`, `description`, `version`,
   and `kind: object`. Prefer a lowercase kebab-case name so its directory
   matches exactly. Otherwise use the name lowercased with runs of non-alphanumeric
   characters replaced by a hyphen and trim leading/trailing hyphens.
3. Call `structured_output schema_file=agent-manifest.schema.v1.json` with
   an instruction to build the front matter for the requested agent. Include
   `tools: []` and `skills: []` when no tools or skills are needed. Choose only
   existing tool names and skill paths; inspect them before assigning them.
4. Add a `system_prompt` string containing the requested Markdown instructions
   to that returned JSON object. Keep `description` as front matter; it is not
   the system prompt. Do not include `system_prompt` in the YAML front matter.
5. Call `json_to_yaml data=<object including system_prompt> body_field=system_prompt`.
   This existing tool produces correctly quoted YAML and the Markdown body.
6. Write the returned `content` to `/agents/user/<name>/SYSTEM.md`.
7. Read the saved manifest back and confirm its name and instructions. Report
   success only after the write succeeds. A tool or validation error is not
   successful creation; correct the manifest and retry.

## Edit an agent

Read its existing `SYSTEM.md` first. Use `edit_file` for a focused change;
all writes and edits validate the front matter and runtime manifest before
persisting. Preserve unrelated fields and the directory/name relationship.
Do not rename an agent in place, since routines may reference its name.

For a larger rewrite, generate schema-conformant front matter with
`structured_output`, preserve existing optional fields, add the updated
`system_prompt`, render with `json_to_yaml`, and write to the same path.

## Tools, skills, and filesystem access

`tools` names the tools exposed to this agent. `skills` contains existing
absolute `/skills/<skill-name>` paths. Agents have no filesystem mounts unless
explicitly declared. Declare required paths in `mounts` with `access: read`
or `read-write`; extra `permissions` must stay within those mounts.
Adding a skill does not grant access to the documents that skill manages.
For example, an agent using `managing-routines` needs a read mount for that
skill and a read-write mount for `/routines/`. Use the same pattern for
`managing-agents` and `/agents/user/` when agent creation is part of its job.

Confirm the saved name and the change briefly. Creating an agent does not
execute it or modify any routine that references it.
