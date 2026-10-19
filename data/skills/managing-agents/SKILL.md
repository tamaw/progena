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

The same object agent can be called through `task` when its answer is needed
before continuing, or through `start_async_task` to run in the background.
No `async` front-matter flag or duplicate definition is needed. Both modes use
the manifest's tools, skills, mounts, permissions, and model configuration.
Use the background tools to check, update, or cancel an existing task; creation
and deletion refresh both delegation menus automatically. Routine `agent`
nodes default to `execution: "wait"`. Set `execution: "background"` to launch a
new conversation and advance with a task reference (task_id, thread_id, run_id,
agent_name, status), rather than the agent response. Its response_schema validates
the eventual task result; task failures are reported separately from the parent run.

Remote HTTP A2A delegation is distinct from object-agent delegation: use
`a2a_send`/`a2a_send_async` for an explicitly named registered remote agent,
then `a2a_status` or `a2a_cancel` for its tracked task.

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

`tools` names the tools exposed to this agent. `skills` lists individual
skills, each an existing `/skills/<skill-name>` folder containing a `SKILL.md`.
Each listed skill is loaded into the agent's prompt and mounted read-only, so
do not repeat it under `mounts`. `skills` never takes a whole folder of skills;
to give an agent read access to every skill, mount `/skills/` instead (that
grants file access only and loads none of them into its prompt).

User agents may list the ACP tools `acp_start`, `acp_send`, `acp_status`,
`acp_answer`, `acp_cancel`, and `acp_list` when they need to drive a Claude ACP
session. Give such an agent the `driving-acp-agents` skill as well; use a
`repo-manager` worktree as the ACP `cwd`, rather than granting it shell or GitHub
credentials.

Agents have no other filesystem access unless declared. Declare required paths
in `mounts` with `access: read` or `read-write`; extra `permissions` must stay
within those mounts or listed skills. Adding a skill does not grant access to
the documents that skill manages. For example, an agent using
`managing-routines` also needs a read-write mount for `/routines/`. Use the same
pattern for `managing-agents` and `/agents/user/` when agent creation is part of
its job. Agents created here may mount `/config/` read-only only, and cannot
enable `shell`, `env_passthrough`, `git_write` or `git_publish`; those are reserved for bundled agents.

Set `memory: true` to give an agent private memory: its `/memories/` is its own
writable folder, and every file in it is loaded into its prompt on every run
(`/memories/AGENTS.md` first). No mount needs declaring; such an agent cannot
also mount the shared `/memories/`. These notes are not loaded into
Progenitor's own memory; Progenitor can inspect them at
`/memories/agents/<name>/`.

Set `knowledge` to a list of `{base, access}` grants. `access: read` is
read-only; `access: write` permits page writes. Knowledge grants are mounted
under `/knowledge/<base>/`, automatically attach the `curating-knowledge`
skill and a `search` tool limited to the granted bases, and isolate sibling
bases. Unknown bases are skipped with a warning in the child result; create
the base or fix the manifest.

Confirm the saved name and the change briefly. Creating an agent does not
execute it or modify any routine that references it.
