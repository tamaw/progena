# Progenitor runtime — agent guide

Progenitor hosts the conversational DeepAgent, maintains durable state, runs
scheduled/background work, and delegates to A2A, ACP and async subagents.

## Ownership

Organize by domain, adapter, or application assembly. Small domains stay flat;
introduce subdirectories only for distinct responsibilities.

```text
progenitor/
├── main.py / config.py      # Entry point and environment configuration
├── agent/
│   ├── factory.py          # DeepAgent composition
│   ├── adapters.py         # Model, tool and graph adapters
│   ├── filesystem/         # Shared file protocol, mounts and domain adapters
│   ├── middleware/
│   ├── object_agents/      # SDK-aware manifests, construction and live registry
│   └── subagents/          # DeepAgent descriptors and live middleware
├── orchestration/
│   ├── graphs/             # Conversation and heartbeat graph assembly
│   ├── nodes/              # Graph execution adapters
│   ├── state.py            # Composed graph state and background-work contract
│   └── store.py            # Background queue operations and advisory lock
├── runtime/
│   ├── progenitor.py       # Process lifecycle and live refresh wiring
│   ├── heartbeat.py        # Loop execution
│   ├── http/               # Server, chat execution and response contracts
│   └── streaming/          # DeepAgent stream translation and SSE envelopes
├── workflows/              # Contracts, ports, derivation, validation and store
├── routines/               # Graph compilation, execution and documents
├── schedules/              # Contracts, dispatch, validation and store
├── repositories/           # Contracts, scanning and store
├── sessions/               # Context, messages, history, transcript and store
├── attachments/            # Uploaded attachment persistence
├── tasks/ / inbox/         # Document rules, Markdown representations and stores
├── alerts/ / modules/      # Domain contracts and document operations
├── settings/               # Providers, profiles, preferences and policies
├── instructions/ / knowledge/ / plugins/
├── registry/ / a2a/ / acp/ / async_subagents/ / mcp/
├── llm/                    # Provider context, model calls, schemas and parsing
├── documents/              # Shared keys, kinds, types, schemas and content access
├── persistence/            # Connections, checkpoints, native store, cache and listener
├── security/               # Secret redaction
├── state/manager.py        # Existing facade and state lifecycle assembly
└── tools/                  # Agent-callable entry points and dynamic discovery
```

Feature names follow the backend/frontend where ownership matches. Sessions
represent persisted conversation history; agent/ represents DeepAgent integration.
Protocol-specific A2A and ACP behavior keeps explicit ownership.

## Dependencies

- Domain contracts and validation must import without constructing the agent,
  server, graph or database connection. Import directly from the owning module;
  package initializers must not eagerly assemble unrelated capabilities.
- Domain stores own document interpretation and mutations. They receive the
  shared persistence.documents.StateDocuments access object, not StateManager.
  This preserves one cache and the original read-through behavior across stores.
- StateManager assembles those stores and retains existing call signatures.
  Put new domain behavior in its store, not in the facade. Keep lifecycle,
  per-thread mutation locks and cache flushing coordinated centrally.
- Agent filesystem code adapts native read/write/edit operations to domain
  rules. Schedule validation belongs in schedules/validation.py; task Markdown
  belongs in tasks/documents.py. Shared file protocol mechanics belong in
  agent/filesystem/base.py.
- Graph nodes and tools adapt domain capabilities. Runtime code owns lifecycle,
  queue coordination and HTTP/stream transport.
- Provider/profile validation belongs in settings; model resolution and calls
  belong in the LLM/agent integration. SDK-specific object-agent manifest parsing
  remains under agent/object_agents/; plugin installation consumes it.

## Workflows

workflows/models.py owns Pydantic contracts. ports.py constructs node port
schemas; derivation.py recomputes authoritative schemas from document types,
instruction templates and routines. validation.py checks payloads and graph
compatibility; graph.py owns ordering/cycles. Document IDs, defaults and list
projections live in documents.py; trigger payloads live in events.py.

The shared documents/schema.py helper embeds nominally typed document schemas,
hoisting definitions so local references resolve. Instruction templates use this
helper and do not import workflow implementation code.

## Persistence and lifecycle

Production injects the Postgres LangGraph checkpointer and store. Runtime state,
mutable configuration, memories, instructions, sessions and virtual files are
Postgres documents. Bundled schemas, native skills and native agent manifests
remain repository-managed assets. Preserve document keys, namespaces, seed
behavior, virtual mount paths and LISTEN/NOTIFY contracts when moving code.
Repository scans and background work retain read-through access.

Keep the lazy model aliases in state/models.py and workflows/__init__.py for
historical LangGraph checkpoints that contain old Python class paths. They are
deserialization compatibility only; application imports use current domain paths.
Persistence readers use CheckpointSerializer to translate historical
app.progenitor.* constructor identities to src.progenitor.* without rewriting
the database or changing message contents. Use it when opening checkpoint savers
or decoding checkpoint blobs directly.

Workspace and shell directories are allocated dynamically. The tools registry
discovers implementations directly under tools/; moving a tool into a nested
directory requires updating discovery rather than silently dropping the tool.

## Validation

Activate the project virtual environment before Python commands:

```bash
source .venv/bin/activate
ruff check src/backend src/progenitor tests/backend tests/progenitor
pyright --pythonpath .venv/bin/python src/
pytest tests/backend tests/progenitor --timeout=45
```

Use fake model graphs for unit tests. Persistence tests create disposable
Postgres databases; never point them at production. Cover request correlation,
resumed runs, per-thread isolation, virtual file validation, read-through
coherence and queue locking when changing their owners.

Create PRs for review. Do not merge or deploy unless the human explicitly asks.
