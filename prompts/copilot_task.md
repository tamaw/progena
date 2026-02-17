# Copilot Build Task

Create the following component now. Do not ask for clarification - just build it:

## Task
Create a new {kind} named "{agent_name}" in `build/agents/{agent_name}/`

## What to create
1. Create directory: `build/agents/{agent_name}/`
2. Create `build/agents/{agent_name}/agent.py` with the main implementation
3. Create `build/agents/{agent_name}/__init__.py` that exports the main class

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
- The agent class must have:
  - AGENT_ID, AGENT_NAME, DESCRIPTION, CAPABILITIES class attributes
  - async def invoke(self, input: str) -> str method
  - def get_agent_card(self) -> dict method

Build it now.
