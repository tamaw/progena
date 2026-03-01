# Copilot Build Task

Create the following component now. Do not ask for clarification - just build it:

## Task
Create a new {kind} named "{agent_name}" in `app/agents/{agent_name}/`

## What to create
1. Create directory: `app/agents/{agent_name}/`
2. Create `app/agents/{agent_name}/agent.py` with the main implementation
3. Create `app/agents/{agent_name}/__init__.py` that exports the main class

## Description
{description}

## Capabilities
{capabilities}

## Implementation Notes
{implementation_notes}

{structure_guidance}

## Requirements
- Follow Python best practices
- Include docstrings and type hints
- Implement an HTTP A2A agent using FastAPI (preferred) and follow prompts/agent_structure.md:
  - `agent.py` must expose `app = FastAPI()` and implement:
    - GET `/.well-known/agent.json`
    - GET `/health`
    - POST `/` with `{request, context}` and respond with `{success, response, error, metadata}`
  - Emit stdout status lines: `STATUS:<status>:<message>`
  - Keep the `response` field plain text unless the task explicitly requires structured output.
- If the agent is LLM-backed, use the same Azure `/responses` integration pattern as Progenitor:
  - Read `AZURE_OPENAI_BASE_URL` + `AZURE_OPENAI_API_KEY`
  - Use `PROGENITOR_MODEL_DEFAULT` for model selection
  - Support multi-round tool calling (LangGraph is acceptable for this loop) to gather data before responding

Build it now.
