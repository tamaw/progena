# DeepAgents Developer Guide

Short-form inventory of the Python monorepo. This guide favors breadth over depth and points to the main classes, functions, and models worth grepping.

## Package Map

### `deepagents` (`libs/deepagents`)
- Purpose: batteries-included agent harness built on LangGraph.
- Main exports: `create_deep_agent`, `FilesystemMiddleware`, `MemoryMiddleware`, `SubAgentMiddleware`, `AsyncSubAgentMiddleware`, `SubAgent`, `CompiledSubAgent`, `AsyncSubAgent`.
- Main themes: planning, filesystem tools, shell access, sub-agents, skills, memory, summarization.

### `deepagents-cli` (`libs/cli`)
- Purpose: terminal app and headless CLI for Deep Agents.
- Main areas: interactive TUI, non-interactive execution, MCP config loading, skill commands, model/config management, session handling.

### `deepagents-acp` (`libs/acp`)
- Purpose: Agent Client Protocol server integration around Deep Agents.
- Main export: `AgentServerACP`.

### `deepagents-evals` (`libs/evals`)
- Purpose: evaluation helpers and Harbor integration.
- Main exports: `HarborSandbox`, `DeepAgentsWrapper`, `FailureCategory`, `InfraMetadata`, LangSmith dataset/experiment helpers.

### `langchain-repl` (`libs/repl`)
- Purpose: lightweight interpreter and middleware for REPL-style tool execution.
- Main exports: `Interpreter`, `ReplMiddleware`.

## Core Agent Features

### Agent factory
- Entry function: `create_deep_agent(...)`.
- Use when: you want the opinionated default Deep Agent graph.
- Main knobs: model, tools, middleware, subagents, async subagents, memory, skills, system prompt, checkpointer.
- File: `libs/deepagents/deepagents/graph.py`.

### Default model selection
- Entry function: `get_default_model()`.
- Use when: no explicit model is passed.
- Default backend behavior: returns Anthropic-backed chat model configuration.
- File: `libs/deepagents/deepagents/graph.py`.

### Model resolution helpers
- Entry functions: `resolve_model`, `get_model_identifier`, `model_matches_spec`.
- Use when: normalizing model inputs or applying model-specific behavior.
- File: `libs/deepagents/deepagents/_models.py`.

### OpenRouter handling
- Entry functions: `check_openrouter_version`, `_openrouter_attribution_kwargs`.
- Use when: provider-specific OpenRouter support is needed.
- File: `libs/deepagents/deepagents/_models.py`.

## Middleware Features

### Filesystem middleware
- Entry class: `FilesystemMiddleware`.
- Use when: the agent should read, write, edit, grep, glob, list files, and execute shell commands.
- Key methods: `wrap_model_call`, `awrap_model_call`, `wrap_tool_call`, `awrap_tool_call`.
- Tool builders: `_create_ls_tool`, `_create_read_file_tool`, `_create_write_file_tool`, `_create_edit_file_tool`, `_create_glob_tool`, `_create_grep_tool`, `_create_execute_tool`.
- File: `libs/deepagents/deepagents/middleware/filesystem.py`.

### Filesystem state
- Entry model: `FilesystemState`.
- Use when: tool-read file contents should persist in graph state.
- Main field: `files`.
- File: `libs/deepagents/deepagents/middleware/filesystem.py`.

### Filesystem tool schemas
- Entry models: `LsSchema`, `ReadFileSchema`, `WriteFileSchema`, `EditFileSchema`, `GlobSchema`, `GrepSchema`, `ExecuteSchema`.
- Use when: validating tool-call inputs.
- File: `libs/deepagents/deepagents/middleware/filesystem.py`.

### Large output compaction in filesystem middleware
- Entry methods: `_process_large_message`, `_evict_and_truncate_messages`, `_intercept_large_tool_result`.
- Use when: file/tool outputs are too large for the current context window.
- File: `libs/deepagents/deepagents/middleware/filesystem.py`.

### Subagent middleware
- Entry class: `SubAgentMiddleware`.
- Use when: the main agent should delegate isolated tasks to sub-agents.
- Key methods: `_get_subagents`, `wrap_model_call`, `awrap_model_call`.
- File: `libs/deepagents/deepagents/middleware/subagents.py`.

### Subagent spec
- Entry model: `SubAgent`.
- Use when: defining named synchronous subagents.
- Main fields: `name`, `description`, `system_prompt`, `tools`, `model`, `middleware`, `interrupt_on`, `skills`.
- File: `libs/deepagents/deepagents/middleware/subagents.py`.

### Precompiled subagent spec
- Entry model: `CompiledSubAgent`.
- Use when: passing a custom runnable/graph instead of a plain spec.
- Main fields: `name`, `description`, `runnable`.
- File: `libs/deepagents/deepagents/middleware/subagents.py`.

### Task tool schema
- Entry model: `TaskToolSchema`.
- Use when: validating the `task` delegation tool.
- Main fields: `description`, `subagent_type`.
- File: `libs/deepagents/deepagents/middleware/subagents.py`.

### Async subagent middleware
- Entry class: `AsyncSubAgentMiddleware`.
- Use when: background work should be delegated to remote Agent Protocol / LangGraph servers.
- Key methods: `wrap_model_call`, `awrap_model_call`.
- File: `libs/deepagents/deepagents/middleware/async_subagents.py`.

### Async subagent spec
- Entry model: `AsyncSubAgent`.
- Use when: defining remote background subagents.
- Main fields: `name`, `description`, `graph_id`, `url`, `headers`.
- File: `libs/deepagents/deepagents/middleware/async_subagents.py`.

### Async task state
- Entry models: `AsyncTask`, `AsyncSubAgentState`.
- Use when: persisting background run metadata in the main graph state.
- Main fields: `task_id`, `agent_name`, `thread_id`, `run_id`, `status`, timestamps.
- File: `libs/deepagents/deepagents/middleware/async_subagents.py`.

### Async task schemas
- Entry models: `StartAsyncTaskSchema`, `CheckAsyncTaskSchema`, `UpdateAsyncTaskSchema`, `CancelAsyncTaskSchema`, `ListAsyncTasksSchema`.
- Use when: validating remote task lifecycle tools.
- File: `libs/deepagents/deepagents/middleware/async_subagents.py`.

### Async subagent client cache
- Entry class: `_ClientCache`.
- Use when: reusing LangGraph SDK clients by `(url, headers)`.
- Key methods: `get_sync`, `get_async`.
- File: `libs/deepagents/deepagents/middleware/async_subagents.py`.

### Memory middleware
- Entry class: `MemoryMiddleware`.
- Use when: always-on project/user memory should be injected from `AGENTS.md` files.
- Key methods: `before_agent`, `abefore_agent`, `modify_request`, `wrap_model_call`, `awrap_model_call`.
- File: `libs/deepagents/deepagents/middleware/memory.py`.

### Memory state
- Entry models: `MemoryState`, `MemoryStateUpdate`.
- Use when: storing loaded memory contents in state without exposing them in final outputs.
- Main field: `memory_contents`.
- File: `libs/deepagents/deepagents/middleware/memory.py`.

### Skills middleware
- Entry class: `SkillsMiddleware`.
- Use when: the agent should discover and advertise reusable `SKILL.md` workflows.
- Key methods: `_format_skills_locations`, `_format_skills_list`, `modify_request`, `before_agent`, `abefore_agent`, `wrap_model_call`, `awrap_model_call`.
- File: `libs/deepagents/deepagents/middleware/skills.py`.

### Skills models
- Entry models: `SkillMetadata`, `SkillsState`, `SkillsStateUpdate`.
- Use when: tracking discovered skills and private skill metadata.
- File: `libs/deepagents/deepagents/middleware/skills.py`.

### Skill parsing helpers
- Entry functions: `_validate_skill_name`, `_parse_skill_metadata`, `_validate_metadata`, `_format_skill_annotations`, `_list_skills`.
- Use when: reading and validating skill directories.
- File: `libs/deepagents/deepagents/middleware/skills.py`.

### Summarization middleware
- Entry functions: `create_summarization_middleware`, `create_summarization_tool_middleware`.
- Use when: conversation or tool history needs compaction.
- File: `libs/deepagents/deepagents/middleware/summarization.py`.

### Summarization tool middleware
- Entry class: `SummarizationToolMiddleware`.
- Use when: compaction should be exposed as a tool-like capability.
- Key methods: `_create_compact_tool`, `_run_compact`, `_arun_compact`, `wrap_model_call`, `awrap_model_call`.
- File: `libs/deepagents/deepagents/middleware/summarization.py`.

### Summarization state and settings
- Entry models: `CompactConversationSchema`, `SummarizationEvent`, `TruncateArgsSettings`, `SummarizationState`, `SummarizationDefaults`.
- Use when: controlling when and how compaction happens.
- File: `libs/deepagents/deepagents/middleware/summarization.py`.

### Tool-call patching middleware
- Entry class: `PatchToolCallsMiddleware`.
- Use when: tool-call payloads need normalization or patching before execution.
- File: `libs/deepagents/deepagents/middleware/patch_tool_calls.py`.

## Backend Features

### Backend contract
- Entry class: `BackendProtocol`.
- Use when: implementing a storage/sandbox backend compatible with Deep Agents.
- Key methods: `ls`, `read`, `grep`, `glob`, `write`, `edit`, `upload_files`, `download_files`, `ls_info`, `glob_info`, `grep_raw`, plus async variants.
- File: `libs/deepagents/deepagents/backends/protocol.py`.

### Sandbox backend contract
- Entry class: `SandboxBackendProtocol`.
- Use when: a backend also supports command execution.
- Companion helper: `execute_accepts_timeout`.
- File: `libs/deepagents/deepagents/backends/protocol.py`.

### Backend result models
- Entry models: `FileDownloadResponse`, `FileUploadResponse`, `FileInfo`, `GrepMatch`, `FileData`, `ReadResult`, `WriteResult`, `EditResult`, `LsResult`, `GrepResult`, `GlobResult`, `ExecuteResponse`.
- Use when: normalizing backend operations and tool results.
- File: `libs/deepagents/deepagents/backends/protocol.py`.

### Filesystem backend
- Entry class: `FilesystemBackend`.
- Use when: local filesystem access is sufficient.
- Key methods: `_resolve_path`, `_to_virtual_path`, `ls`, `read`, `write`, `edit`, `grep`, `glob`, `upload_files`, `download_files`.
- File: `libs/deepagents/deepagents/backends/filesystem.py`.

### Local shell backend
- Entry class: `LocalShellBackend`.
- Use when: local filesystem plus local shell execution are both desired.
- Base types: `FilesystemBackend`, `SandboxBackendProtocol`.
- File: `libs/deepagents/deepagents/backends/local_shell.py`.

### Base sandbox
- Entry class: `BaseSandbox`.
- Use when: implementing a remote or isolated execution environment.
- Base type: `SandboxBackendProtocol`.
- File: `libs/deepagents/deepagents/backends/sandbox.py`.

### Composite backend
- Entry class: `CompositeBackend`.
- Use when: multiple mounted backends should appear as one filesystem/tool surface.
- Key methods: `_get_backend_and_key`, `ls`, `read`, `grep`, `glob`, `write`, `edit`, `execute`, `upload_files`, `download_files`, plus async mirrors.
- File: `libs/deepagents/deepagents/backends/composite.py`.

### State/store/langsmith backends
- Files: `backends/state.py`, `backends/store.py`, `backends/langsmith.py`.
- Use when: bridging runtime state, persistent store, or LangSmith-aware integrations into backend behavior.

## CLI Features

### CLI entrypoint
- Entry function: `cli_main()`.
- Use when: running the terminal client.
- Companion functions: `parse_args`, `apply_stdin_pipe`, `_print_session_stats`, `_check_mcp_project_trust`.
- File: `libs/cli/deepagents_cli/main.py`.

### Dependency checks
- Entry functions: `check_cli_dependencies`, `check_optional_tools`.
- Use when: validating local requirements like `rg` and related helpers.
- File: `libs/cli/deepagents_cli/main.py`.

### Interactive TUI app
- Entry class: `DeepAgentsApp`.
- Use when: running the full Textual-based terminal interface.
- Supporting models: `QueuedMessage`, `DeferredAction`, `TextualSessionState`, `AppResult`.
- File: `libs/cli/deepagents_cli/app.py`.

### CLI agent factory
- Entry function: `create_cli_agent(...)`.
- Use when: building the packaged coding-assistant style agent used by the terminal app.
- Supporting functions: `load_async_subagents`, `list_agents`, `reset_agent`, `get_system_prompt`.
- File: `libs/cli/deepagents_cli/agent.py`.

### Shell allowlist middleware
- Entry class: `ShellAllowListMiddleware`.
- Use when: CLI shell execution needs command gating or approval policy.
- File: `libs/cli/deepagents_cli/agent.py`.

### MCP tool config loading
- Entry functions: `load_mcp_config`, `discover_mcp_configs`, `classify_discovered_configs`, `extract_stdio_server_commands`, `merge_mcp_configs`, `load_mcp_config_lenient`.
- Entry models: `MCPToolInfo`, `MCPServerInfo`, `MCPSessionManager`.
- Use when: CLI should discover and merge MCP servers from project/user config.
- File: `libs/cli/deepagents_cli/mcp_tools.py`.

### Skill commands
- Entry functions: `setup_skills_parser`, `execute_skills_command`.
- Subcommands/helpers: `_list`, `_create`, `_info`, `_delete`, `_generate_template`.
- Use when: managing local CLI skills from the command line.
- File: `libs/cli/deepagents_cli/skills/commands.py`.

### Non-interactive mode
- Entry area: `non_interactive.py`.
- Use when: running the CLI headlessly in CI or scripts.
- Main models: `HITLIterationLimitError`, `_ConsoleSpinner`, `StreamState`, `ThreadUrlLookupState`.
- Main functions: `_process_interrupts`, `_process_ai_message`, `_process_stream_chunk`, `_make_hitl_decision`.

### CLI server/client support
- Key files: `remote_client.py`, `server.py`, `server_graph.py`, `server_manager.py`, `sessions.py`.
- Use when: the terminal app talks to a background server process or manages persistent sessions.

### Sandbox integrations
- Key files: `integrations/sandbox_factory.py`, `integrations/sandbox_provider.py`.
- Use when: selecting sandbox providers for execution backends in CLI mode.

### UI widgets
- Key package: `widgets/`.
- Use when: extending or debugging the Textual UI.
- Examples: `approval.py`, `chat_input.py`, `mcp_viewer.py`, `messages.py`, `model_selector.py`, `thread_selector.py`, `tool_renderers.py`.

## ACP Features

### ACP server wrapper
- Entry class: `AgentServerACP`.
- Use when: exposing a Deep Agent over Agent Client Protocol.
- Key methods: `initialize`, `new_session`, `set_session_mode`, `set_config_option`, `cancel`, `prompt`, `_handle_interrupts`.
- File: `libs/acp/deepagents_acp/server.py`.

### ACP session context
- Entry model: `AgentSessionContext`.
- Use when: tracking ACP-side session state.
- File: `libs/acp/deepagents_acp/server.py`.

## REPL Features

### Interpreter
- Entry class: `Interpreter`.
- Use when: executing a constrained scripting language inside the agent.
- Key methods: `evaluate`, `parse`, `clear_output`, `_eval_program`, `_eval_statement`, `_eval_expression`, `_eval_print`, `_eval_parallel`, `_eval_try`.
- File: `libs/repl/langchain_repl/interpreter.py`.

### REPL AST and parser models
- Entry models: `Token`, `Statement`, `Expression`, `Program`, `Assign`, `IfStatement`, `ForStatement`, `ExpressionStatement`, `Name`, `Literal`, `ListLiteral`, `DictLiteral`, `Call`, `Index`, `ParseError`.
- Use when: extending the custom REPL language.
- File: `libs/repl/langchain_repl/interpreter.py`.

### REPL middleware
- Entry class: `ReplMiddleware`.
- Use when: exposing the interpreter as an agent tool/middleware layer.
- Key methods: `modify_request`, `wrap_model_call`, `awrap_model_call`, `_create_repl_tool`, `_run_interpreter`.
- File: `libs/repl/langchain_repl/middleware.py`.

## Eval Features

### Harbor sandbox
- Entry class: `HarborSandbox`.
- Use when: running Deep Agents against Harbor-style evaluation environments.
- Key methods: `execute`, `aexecute`, `id`, `aread`.
- File: `libs/evals/deepagents_harbor/backend.py`.

### Deep Agents eval wrapper
- Entry class: `DeepAgentsWrapper`.
- Use when: adapting a Deep Agent to a benchmark/eval harness.
- Key methods: `setup`, `run`, `version`, `_save_trajectory`, `_get_formatted_system_prompt`.
- File: `libs/evals/deepagents_harbor/deepagents_wrapper.py`.

### Harbor integration exports
- Entry exports: `add_feedback`, `create_dataset`, `create_example_id_from_instruction`, `create_experiment`, `ensure_dataset`.
- Use when: connecting eval runs to LangSmith datasets and experiments.
- File: `libs/evals/deepagents_harbor/__init__.py`.

### Shared eval utilities
- Package: `deepagents_evals`.
- Use when: benchmark suites need shared harness logic across datasets/tasks.

## Main Models Worth Knowing

### Core spec models
- `SubAgent`
- `CompiledSubAgent`
- `AsyncSubAgent`
- `AsyncTask`

### Filesystem tool models
- `FilesystemState`
- `LsSchema`
- `ReadFileSchema`
- `WriteFileSchema`
- `EditFileSchema`
- `GlobSchema`
- `GrepSchema`
- `ExecuteSchema`

### Memory/skills/summarization models
- `MemoryState`
- `MemoryStateUpdate`
- `SkillMetadata`
- `SkillsState`
- `SkillsStateUpdate`
- `SummarizationState`
- `SummarizationDefaults`
- `SummarizationEvent`

### Backend result models
- `FileInfo`
- `FileData`
- `ReadResult`
- `WriteResult`
- `EditResult`
- `LsResult`
- `GrepResult`
- `GlobResult`
- `ExecuteResponse`
- `FileUploadResponse`
- `FileDownloadResponse`

### CLI/MCP models
- `MCPToolInfo`
- `MCPServerInfo`
- `MCPSessionManager`
- `QueuedMessage`
- `DeferredAction`
- `TextualSessionState`
- `AppResult`
- `StreamState`

## Useful Reading Order

1. `libs/deepagents/deepagents/graph.py`
2. `libs/deepagents/deepagents/middleware/filesystem.py`
3. `libs/deepagents/deepagents/middleware/subagents.py`
4. `libs/deepagents/deepagents/middleware/async_subagents.py`
5. `libs/deepagents/deepagents/middleware/memory.py`
6. `libs/deepagents/deepagents/middleware/skills.py`
7. `libs/cli/deepagents_cli/agent.py`
8. `libs/cli/deepagents_cli/main.py`
