# LangGraph Developer Guide

Short-form inventory of the Python monorepo under `libs/`. This guide optimizes for coverage over depth.

## Package Map

### `langgraph` (`libs/langgraph`)
- Purpose: Core orchestration framework for durable stateful graphs.
- Main exports: `StateGraph`, `MessageGraph`, `MessagesState`, `add_messages`, `START`, `END`.
- Main models/types: `RetryPolicy`, `CachePolicy`, `Interrupt`, `StateUpdate`, `StateSnapshot`, `Send`, `Command`, `Overwrite`, `GraphOutput`.

### `langgraph-prebuilt` (`libs/prebuilt`)
- Purpose: Higher-level agent and tool helpers on top of core graphs.
- Main exports: `create_react_agent`, `ToolNode`, `tools_condition`, `ValidationNode`, `InjectedState`, `InjectedStore`, `ToolRuntime`.
- Main models/types: `AgentState`, `AgentStatePydantic`, `AgentStateWithStructuredResponse`, `ToolCallRequest`, `ToolCallWithContext`.

### `langgraph-checkpoint` (`libs/checkpoint`)
- Purpose: Base persistence, memory, store, serializer, and cache interfaces.
- Main exports: `BaseCheckpointSaver`, `Checkpoint`, `CheckpointTuple`, `CheckpointMetadata`, `InMemorySaver`, `BaseStore`, `InMemoryStore`, `BaseCache`, `InMemoryCache`.
- Main models/types: `Item`, `SearchItem`, `GetOp`, `SearchOp`, `PutOp`, `ListNamespacesOp`, `IndexConfig`, `TTLConfig`.

### `langgraph-checkpoint-postgres` (`libs/checkpoint-postgres`)
- Purpose: Postgres-backed checkpoint and store adapters.
- Main exports: `PostgresSaver`, `AsyncPostgresSaver`, `ShallowPostgresSaver`, `PostgresStore`, `AsyncPostgresStore`, `PoolConfig`.
- Main models/types: same checkpoint/store models as base package.

### `langgraph-checkpoint-sqlite` (`libs/checkpoint-sqlite`)
- Purpose: SQLite-backed checkpoint, store, and cache adapters.
- Main exports: `SqliteSaver`, `AsyncSqliteSaver`, `SqliteStore`, `AsyncSqliteStore`, `SqliteCache`.
- Main models/types: same checkpoint/store/cache models as base package.

### `langgraph-sdk` (`libs/sdk-py`)
- Purpose: Client SDK for LangGraph API servers.
- Main exports: `get_client`, `get_sync_client`, `Auth`, `Encryption`, `EncryptionContext`.
- Main models/types: `Assistant`, `AssistantVersion`, `Thread`, `Run`, `Cron`, `Checkpoint`, `GraphSchema`, `Command`, `Send`, many stream event payload models.

### `langgraph-cli` (`libs/cli`)
- Purpose: Local dev, Docker image, and deployment workflows for LangGraph apps.
- Main commands: `up`, `build`, `dockerfile`, `dev`, `new`, `prepare`.
- Main models/files: `langgraph.json`, Docker build context, generated Dockerfile.

### `langgraph-checkpoint-conformance` (`libs/checkpoint-conformance`)
- Purpose: Test suite/spec for validating custom checkpointer implementations.
- Main models/types: conformance specs and fixtures for checkpoint saver behavior.

## Core Graph Features

### Graph builder
- Entry class: `StateGraph`.
- Use when: you want shared-state node orchestration.
- Key functions: `add_node`, `add_edge`, `add_conditional_edges`, `add_sequence`, `set_entry_point`, `set_conditional_entry_point`, `set_finish_point`, `validate`, `compile`.
- Main models: `CompiledStateGraph`, `StateGraph`, `START`, `END`.

### Executable graph
- Entry object: `CompiledStateGraph`.
- Use when: you have already called `compile()`.
- Main runtime methods: inherited graph execution methods such as `invoke`, `ainvoke`, `stream`, `astream`, `batch`.
- Main models: `GraphOutput`, `StateSnapshot`, checkpoint stream payloads.

### Message-first graph
- Entry classes: `MessageGraph`, `MessagesState`.
- Use when: state is mainly chat history.
- Key functions: `add_messages`.
- Main models: `MessagesState`, `AnyMessage`, `BaseMessage`, `RemoveMessage`.

### Message reducer
- Entry function: `add_messages(left, right, format=None)`.
- Use when: multiple nodes append or overwrite messages by id.
- Key behavior: append-only by default, overwrite on matching `id`, supports `REMOVE_ALL_MESSAGES`.
- Main models: `Messages`, `BaseMessage`, `BaseMessageChunk`, `MessageLikeRepresentation`.

### Functional API
- Entry decorators: `task`, `entrypoint`.
- Use when: you prefer function-first workflows over explicit graph builder code.
- Key functions: `task(...)`, `entrypoint(...)`.
- Main models: `_TaskFunction`, `entrypoint`.

### Routing and control flow
- Entry models: `Command`, `Send`.
- Use when: a node needs to control execution, branch, or emit directed sends.
- Key functions: `interrupt(value)`.
- Main models: `Command`, `Send`, `Interrupt`, `Overwrite`.

### Retry policy
- Entry model: `RetryPolicy`.
- Use when: node execution should retry on specific failures.
- Key fields: retry limits, retry predicates, backoff configuration.
- Main models: `RetryPolicy`.

### Cache policy
- Entry model: `CachePolicy`.
- Use when: node outputs or subgraph work should be cacheable.
- Key fields: cache key function and cache behavior.
- Main models: `CachePolicy`, `CacheKey`.

### Streaming
- Entry types: `StreamMode`, `StreamWriter`.
- Use when: you need incremental output rather than final return values.
- Modes: `values`, `updates`, `messages`, `checkpoints`, `tasks`, `debug`, `custom`.
- Main models: `ValuesStreamPart`, `UpdatesStreamPart`, `MessagesStreamPart`, `CheckpointStreamPart`, `TasksStreamPart`, `DebugStreamPart`.

### Interrupts and resume
- Entry function: `interrupt(value)`.
- Use when: a graph should pause for human input or external approval.
- Main models: `Interrupt`, `CheckpointPayload`, `StateSnapshot`.

## Prebuilt Agent Features

### ReAct agent builder
- Entry function: `create_react_agent(...)`.
- Use when: you want a ready-made chat agent with tools.
- Key knobs: model, tools, prompt, structured response schema, store, checkpointer.
- Main models: `AgentState`, `AgentStatePydantic`, `AgentStateWithStructuredResponse`.

### Tool execution node
- Entry class: `ToolNode`.
- Use when: model tool calls should be executed as graph work.
- Key functions: `tools_by_name`, `_parse_input`, `_execute_tool_sync`, `_execute_tool_async`, `_inject_tool_args`, `_validate_tool_call`.
- Main models: `ToolCallRequest`, `ToolCallWithContext`, `ToolRuntime`.

### Tool routing helper
- Entry function: `tools_condition`.
- Use when: conditional edges should branch to a tool node only when tool calls are present.
- Main models: tool call message payloads and `ToolNode`.

### Validation node
- Entry class: `ValidationNode`.
- Use when: tool inputs or structured outputs need validation before execution.
- Main models: validation errors, tool schemas.

### State injection into tools
- Entry model: `InjectedState`.
- Use when: a tool should receive graph state without the model generating it.
- Main models: `InjectedState`, `ToolRuntime`.

### Store injection into tools
- Entry model: `InjectedStore`.
- Use when: a tool should receive long-term memory/store access without exposing internals to the model.
- Main models: `InjectedStore`, `BaseStore`, `ToolRuntime`.

## Checkpoint Features

### Checkpoint base contract
- Entry class: `BaseCheckpointSaver`.
- Use when: you implement a custom saver or need persistence abstraction.
- Key functions: `get`, `get_tuple`, `list`, `put`, `put_writes`, `delete_thread`, `delete_for_runs`, `copy_thread`, `prune`, plus async variants.
- Main models: `Checkpoint`, `CheckpointTuple`, `CheckpointMetadata`, `PendingWrite`.

### Checkpoint metadata
- Entry model: `CheckpointMetadata`.
- Use when: you need run lineage and checkpoint provenance.
- Main fields: `source`, `step`, `parents`, `run_id`.

### Checkpoint tuple
- Entry model: `CheckpointTuple`.
- Use when: you want checkpoint data plus metadata, parent config, and pending writes together.
- Main fields: `config`, `checkpoint`, `metadata`, `parent_config`, `pending_writes`.

### In-memory checkpoint saver
- Entry class: `InMemorySaver`.
- Use when: tests, local debugging, or disposable state are enough.
- Key functions: `get_tuple`, `list`, `put`, `put_writes`, `delete_thread`.
- Main models: same as base checkpoint package.

### Postgres checkpoint saver
- Entry classes: `PostgresSaver`, `AsyncPostgresSaver`, `ShallowPostgresSaver`.
- Use when: production-grade durable execution needs a relational backend.
- Key functions: `from_conn_string`, `setup`, `list`, `get_tuple`, `put`, `put_writes`.
- Main models: same as base checkpoint package.

### SQLite checkpoint saver
- Entry classes: `SqliteSaver`, `AsyncSqliteSaver`.
- Use when: you want file-backed persistence without Postgres.
- Key functions: `cursor`, `setup`, `get_tuple`, `list`, `put`, `put_writes`.
- Main models: same as base checkpoint package.

## Store Features

### Store base contract
- Entry class: `BaseStore`.
- Use when: you need long-term memory or document/key-value storage.
- Key functions: `batch`, `abatch`, `get`, `search`, `put`, `delete`, `list_namespaces`, plus async variants.
- Main models: `Item`, `SearchItem`, `GetOp`, `SearchOp`, `PutOp`, `ListNamespacesOp`.

### Stored item model
- Entry model: `Item`.
- Use when: reading or returning store records.
- Main fields: `namespace`, `key`, `value`, `created_at`, `updated_at`.

### Search result model
- Entry model: `SearchItem`.
- Use when: ranked retrieval should return score metadata.
- Main fields: `score` plus all `Item` fields.

### Point lookup operation
- Entry model: `GetOp`.
- Use when: batching exact namespace/key reads.
- Main fields: `namespace`, `key`, `refresh_ttl`.

### Search operation
- Entry model: `SearchOp`.
- Use when: batching filtered or semantic search.
- Main fields: query text, filter config, limit, offset, namespace prefix.

### Namespace listing operation
- Entry model: `ListNamespacesOp`.
- Use when: browsing store hierarchy.
- Main fields: match conditions, max depth, limit, offset.

### Put operation
- Entry model: `PutOp`.
- Use when: batching inserts, updates, or deletes.
- Main fields: `namespace`, `key`, `value`, `index`, `ttl`.

### TTL configuration
- Entry model: `TTLConfig`.
- Use when: store items should expire automatically.
- Main fields: default ttl and refresh semantics.

### Vector index configuration
- Entry model: `IndexConfig`.
- Use when: semantic search should be enabled.
- Main fields: `dims`, `embed`, `fields`.

### In-memory store
- Entry class: `InMemoryStore`.
- Use when: you want fast ephemeral storage and optional vector search.
- Key functions: `put`, `get`, `search`, `list_namespaces`, `batch`, plus async variants.
- Main models: `IndexConfig`, `Item`, `SearchItem`.

### Postgres store
- Entry classes: `PostgresStore`, `AsyncPostgresStore`.
- Use when: persistent long-term memory should survive process restarts.
- Key functions: same store contract as `BaseStore`.
- Main models: `PoolConfig`, `Item`, `SearchItem`.

### SQLite store
- Entry classes: `SqliteStore`, `AsyncSqliteStore`.
- Use when: local file-backed memory is enough.
- Key functions: same store contract as `BaseStore`.
- Main models: `Item`, `SearchItem`.

## Cache Features

### Cache base contract
- Entry class: `BaseCache`.
- Use when: reusable computed values or request-scoped results should be cached.
- Key functions: `get`, `aget`, `set`, `aset`, `clear`, `aclear`.
- Main models: `Namespace`, `FullKey`, `ValueT`.

### In-memory cache
- Entry class: `InMemoryCache`.
- Use when: local process cache is sufficient.
- Key functions: `get`, `set`, `clear`, async mirrors.
- Main models: same cache key types as base cache.

### SQLite cache
- Entry class: `SqliteCache`.
- Use when: you want file-backed cache persistence with TTL support.
- Key functions: `get`, `set`, `clear`, async mirrors.
- Main models: same cache key types as base cache.

## SDK Features

### SDK entrypoints
- Entry functions: `get_client`, `get_sync_client`.
- Use when: you need async or sync access to a LangGraph API server.
- Main models: `LangGraphClient`, `SyncLangGraphClient`.

### Async client root
- Entry class: `LangGraphClient`.
- Use when: talking to LangGraph from async Python.
- Main handles: `.assistants`, `.threads`, `.runs`, `.store`, `.crons`.
- Key functions: `__aenter__`, `__aexit__`, `aclose`.

### Sync client root
- Entry class: `SyncLangGraphClient`.
- Use when: talking to LangGraph from sync Python.
- Main handles: `.assistants`, `.threads`, `.runs`, `.store`, `.crons`.
- Key functions: `__enter__`, `__exit__`, `close`.

### Assistants API
- Entry classes: `AssistantsClient`, `SyncAssistantsClient`.
- Use when: managing deployed or registered assistants.
- Key functions: `get`, `get_graph`, `get_schemas`, `get_subgraphs`, `create`, `update`, `delete`, `search`, `count`, `get_versions`, `set_latest`.
- Main models: `Assistant`, `AssistantVersion`, `AssistantsSearchResponse`, `GraphSchema`.

### Threads API
- Entry classes: `ThreadsClient`, `SyncThreadsClient`.
- Use when: managing conversation threads and graph state containers.
- Key functions: `get`, `create`, `update`, `delete`, `search`, `count`, `copy`, `prune`, `get_state`, `update_state`, `get_history`, `join_stream`.
- Main models: `Thread`, `ThreadState`, `ThreadTask`, `ThreadUpdateStateResponse`, `Checkpoint`.

### Runs API
- Entry classes: `RunsClient`, `SyncRunsClient`.
- Use when: launching, streaming, waiting on, and cancelling runs.
- Key functions: `stream`, `create`, `create_batch`, `wait`, `list`, `get`, `cancel`, `cancel_many`, `join`, `join_stream`, `delete`.
- Main models: `Run`, `RunCreate`, `RunCreateMetadata`, `Command`, `Send`, stream event models.

### Store API
- Entry classes: `StoreClient`, `SyncStoreClient`.
- Use when: talking to the server-side long-term memory API.
- Key functions: `put_item`, `get_item`, `delete_item`, `search_items`, `list_namespaces`.
- Main models: `Item`, `SearchItem`, `SearchItemsResponse`, `ListNamespaceResponse`.

### Cron API
- Entry classes: `CronClient`, `SyncCronClient`.
- Use when: scheduling recurring runs.
- Key functions: `create_for_thread`, `create`, `delete`, `update`, `search`, `count`.
- Main models: `Cron`, `CronUpdate`.

### Auth extension API
- Entry class: `Auth`.
- Use when: adding server-side authentication and authorization handlers.
- Key decorators: `authenticate`, `on`, `on.threads`, `on.threads.create`, `on.store`, `on.assistants`, `on.crons`.
- Main models: `Auth.types.*`, `Auth.exceptions.*`, `MinimalUserDict`, `AuthContext`, filter types.

### Encryption extension API
- Entry class: `Encryption`.
- Use when: adding server-side at-rest encryption hooks.
- Key decorators: `encrypt.blob`, `encrypt.json`, `decrypt.blob`, `decrypt.json`.
- Main models: `EncryptionContext`, `DuplicateHandlerError`.

### SDK schema models
- Run lifecycle models: `RunStatus`, `ThreadStatus`, `DisconnectMode`, `MultitaskStrategy`, `Durability`.
- Graph metadata models: `GraphSchema`, `Checkpoint`, `Config`.
- Stream models: `ValuesStreamPart`, `UpdatesStreamPart`, `MessagesPartialStreamPart`, `MessagesCompleteStreamPart`, `MessagesMetadataStreamPart`, `MessagesTupleStreamPart`, `TasksStreamPart`, `CheckpointsStreamPart`, `DebugStreamPart`, `MetadataStreamPart`.

## CLI Features

### `langgraph up`
- Purpose: bring up a LangGraph app stack.
- Function name: `up`.
- Use when: running app infra locally or against Docker-backed environments.

### `langgraph build`
- Purpose: build deployable images/artifacts.
- Function name: `build`.
- Use when: packaging a LangGraph app for release.

### `langgraph dockerfile`
- Purpose: generate or inspect Dockerfile output.
- Function name: `dockerfile`.
- Use when: customizing container builds.

### `langgraph dev`
- Purpose: local development server flow.
- Function name: `dev`.
- Use when: iterating on a graph app locally.

### `langgraph new`
- Purpose: scaffold a new project/template.
- Function name: `new`.
- Use when: creating a fresh starter app.

### `langgraph prepare`
- Purpose: prepare build args/stdin and deployment inputs.
- Function names: `prepare_args_and_stdin`, `prepare`.
- Use when: scripting builds or CI packaging.

## Conformance Package

### Checkpointer conformance suite
- Purpose: validate that custom saver implementations behave like LangGraph expects.
- Use when: building a third-party saver backend.
- Main models: conformance spec package under `langgraph.checkpoint.conformance.spec`.

## Suggested Reading Order

1. `langgraph`
2. `langgraph-prebuilt`
3. `langgraph-checkpoint`
4. `langgraph-sdk`
5. `langgraph-cli`
