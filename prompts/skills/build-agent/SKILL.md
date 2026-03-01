---
name: build-agent
description: Build A2A agents and MCP tools that follow Progenitor's directory structure, HTTP endpoints, LLM integration, and registry patterns. Use when creating a new agent, adding an MCP tool, or building a new Progenitor component.
allowed-tools: shell git write_file edit_file read_file
---

# Build Agent Skill

## When to Use
- User asks to create a new agent (A2A protocol)
- User asks to create a new MCP tool (FastMCP)
- User asks to build any new component for the Progenitor ecosystem

## Agent Directory Structure (CRITICAL)
All agents MUST go in subdirectories under `app/agents/<agent_name>/`:

```
app/agents/<agent_name>/
├── __init__.py          # Exports the main agent class
├── agent.py             # Main agent implementation (REQUIRED)
├── config.py            # Agent configuration (optional)
├── models.py            # Data models (optional)
└── README.md            # Documentation
```

WRONG: `app/agents/my_agent.py` (file directly in agents/)
CORRECT: `app/agents/my_agent/agent.py` (in subdirectory)

## MCP Tool Directory Structure
```
app/mcp/<tool_name>/
├── __init__.py          # Exports the mcp server and tool functions
├── server.py            # FastMCP server implementation (REQUIRED)
├── config.py            # Tool configuration (optional)
└── tests/
    └── test_<tool_name>.py
```

## Required HTTP Endpoints (A2A Agents)

1. **GET `/.well-known/agent.json`** — Agent Card
2. **GET `/health`** — Health check
3. **POST `/a2a`** — Main task endpoint (accepts `{"message": "...", "context": {...}}`)

## LLM Integration Pattern
- Use Azure OpenAI `/responses` endpoint via httpx
- Headers: `api-key` from `AZURE_OPENAI_API_KEY`
- Model: `PROGENITOR_MODEL_DEFAULT`
- Implement a local tool-calling loop for multi-step reasoning

## Name Extraction
Extract a descriptive snake_case name from the description:
- "hello world agent" → `hello_world`
- "email notification agent" → `email_notifier`
- NEVER use "unnamed" as the name

## Build Discipline
- Keep diffs minimal
- Update tests if behaviour changes
- Make it registrable in registry.json
- Follow existing patterns in `app/agents/` for reference
