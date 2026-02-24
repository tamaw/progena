# Progenitor Architecture Diagram

## System Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                    PROGENITOR SYSTEM                                 │
│                                                                      │
│  ┌──────────────────────────┐     ┌──────────────────────────────┐  │
│  │       PROGENITOR         │     │       BUILDER AGENT          │  │
│  │  (app/progenitor/)       │     │  (app/agents/builder_agent/) │  │
│  │                          │ A2A │                              │  │
│  │  DeepAgent chat harness  │────▶│  A2A server (port 8107)      │  │
│  │  Perpetual outer loop    │     │  Inner build loop            │  │
│  │  Delegation + routing    │     │  OBSERVE→…→LEARN             │  │
│  └──────────────────────────┘     └──────────────────────────────┘  │
│                                                                      │
│  ┌──────────────────────────┐     ┌──────────────────────────────┐  │
│  │     BACKEND / API        │     │       FRONTEND               │  │
│  │  (app/backend/)          │     │  (app/frontend/)             │  │
│  │  FastAPI :8000            │     │  React/Vite :5173            │  │
│  └──────────────────────────┘     └──────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

## Progenitor — Outer Loop

```
┌──────────────────── OUTER LOOP (Perpetual) ─────────────────────────┐
│                                                                       │
│  ┌────────┐  ┌───────────┐  ┌──────────────┐  ┌────────────────┐   │
│  │ INTAKE │─▶│TIME CHECK │─▶│ ENQUEUE DUE  │─▶│    DECIDE      │   │
│  └────────┘  └───────────┘  └──────────────┘  └───────┬────────┘   │
│                                                         │             │
│                        ┌────────────────────────────────┤            │
│                        │                                │            │
│              ANSWER_DIRECTLY                  DELEGATE_A2A           │
│                        │                                │            │
│                        ▼                                ▼            │
│               ┌──────────────┐               ┌─────────────────┐   │
│               │    CHAT      │               │  PICK_NEXT      │   │
│               │  (DeepAgent) │               │  (A2A call to   │   │
│               └──────┬───────┘               │  builder_agent) │   │
│                       │                       └────────┬────────┘   │
│                       └──────────────┬─────────────────┘            │
│                                      ▼                               │
│                              ┌──────────────┐                        │
│                              │   RESPOND    │                        │
│                              └──────┬───────┘                        │
│                                     │                                 │
│                              ┌──────▼───────┐                        │
│                              │     IDLE     │ (exponential backoff)  │
│                              └──────┬───────┘                        │
│                                     └──────────────────▶ (repeat)   │
└───────────────────────────────────────────────────────────────────────┘
```

## DeepAgent Layer (inside Progenitor)

```
┌──────────────────── DEEPAGENT LAYER ─────────────────────────────────┐
│                       (Conversational Graph)                          │
│                                                                       │
│  ┌────────────┐  ┌────────────┐  ┌──────────┐  ┌───────────────┐   │
│  │ Middleware  │  │   Skills   │  │  Memory  │  │   Subagents   │   │
│  │            │  │            │  │          │  │               │   │
│  │ • Redact   │  │ • build    │  │ AGENTS.md│  │ • builder_    │   │
│  │ • Audit    │  │ • schedule │  │ /memories│  │   agent (A2A) │   │
│  │ • Metrics  │  │ • git      │  │ • Store  │  │ • code review │   │
│  │ • Summarize│  │ • reflect  │  │          │  │ • researcher  │   │
│  │ • PatchTC  │  │ • arch     │  │          │  │               │   │
│  └────────────┘  └────────────┘  └──────────┘  └───────────────┘   │
│                                                                       │
│  Tools ──▶ LiteLLM Proxy ──▶ Azure/OpenAI                           │
│  CompositeBackend: LocalShell + StoreBackend + StateBackend          │
└───────────────────────────────────────────────────────────────────────┘
```

## Builder Agent — Inner Loop

The build loop lives entirely inside the Builder Agent (`app/agents/builder_agent/`),
which is a self-contained A2A server with its own Dockerfile, requirements.txt, and state.

```
┌──────────────────── BUILDER AGENT INNER LOOP ────────────────────────┐
│                                                                        │
│  Triggered by: A2A Task from Progenitor (or any A2A client)           │
│  Runs: OBSERVE → THINK → PLAN → BUILD → EXECUTE → VERIFY → LEARN     │
│  Repeats until: score threshold reached or max iterations hit         │
│                                                                        │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐          │
│  │ OBSERVE  │──▶│  THINK   │──▶│   PLAN   │──▶│  BUILD   │          │
│  └──────────┘   └──────────┘   └──────────┘   └────┬─────┘          │
│                                                      │                 │
│                                                      ▼                 │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐          │
│  │  LEARN   │◀──│  VERIFY  │◀──│ EXECUTE  │◀──│  MERGE   │          │
│  └────┬─────┘   └──────────┘   └──────────┘   └──────────┘          │
│       │                                                                │
│       ├──────────────────────────────────────────▶ OBSERVE (loop)     │
│       │                                                                │
│       └──────────────────────────────────────────▶ PUBLISH (done)     │
│                                                    (raises PR)        │
│                                                                        │
│  Checkpointing: SQLite per-build at state_dir/build_checkpoints.db    │
│  thread_id = build_id (each build isolated)                           │
└────────────────────────────────────────────────────────────────────────┘
```

## Service Ports

| Service          | Port  | Notes                          |
|------------------|-------|--------------------------------|
| Backend API      | 8000  | FastAPI, OpenAI-compatible     |
| Frontend         | 5173  | Vite dev server                |
| Builder Agent    | 8107  | A2A JSON-RPC over HTTP         |
| Other agents     | 8080+ | Generic A2A agents             |
| LiteLLM Proxy    | 4000  | Routes to Azure/OpenAI         |

## State Files

```
data/state/
├── current_state.json     # Progenitor: messages, conversation
├── registry.json          # Agent/tool registry (progenitor reads, builder writes)
├── schedule.json          # Scheduled tasks (progenitor)
└── build_checkpoints.db   # Builder agent: SQLite LangGraph checkpoints
```

---

**Key Design Principle**: Progenitor is a stable, minimal orchestrator.
Risky capabilities (code generation, git mutation, shell execution) live in
the isolated Builder Agent — reachable only via the A2A protocol.
