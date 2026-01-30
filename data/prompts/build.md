# Build Specification Prompt

Generate a detailed specification for building a {item_type}.

## NAME EXTRACTION (CRITICAL)
Extract a descriptive snake_case name from the description. Examples:
- "hello world agent" → name: "hello_world"
- "email notification agent" → name: "email_notifier"
- "weather lookup agent" → name: "weather_lookup"

NEVER use "unnamed" as the name - always derive from the description.

## Build Discipline
- Keep diffs minimal
- Update tests if behaviour changes
- Follow Progenitor framework patterns
- Make it registrable in registry.json

## Directory Structure for Agents (CRITICAL)
Agents MUST be created in a subdirectory under `app/agents/<agent_name>/`:

CORRECT structure:
```
app/agents/<agent_name>/
├── __init__.py          # Exports the main agent class
├── agent.py             # Main agent implementation (REQUIRED)
├── config.py            # Agent configuration (optional)
├── models.py            # Data models (optional)
└── README.md            # Documentation
```

WRONG: Creating files directly in app/agents/ like `app/agents/my_agent.py`
CORRECT: Creating files in subdirectory like `app/agents/my_agent/agent.py`

## Agent Implementation Requirements

Each agent is **self-contained** - there is no base class to inherit from. The agent.py file MUST:

1. Define a main agent class with these class attributes:
   - `AGENT_ID` - Unique snake_case identifier
   - `AGENT_NAME` - Human-readable name
   - `DESCRIPTION` - What the agent does
   - `CAPABILITIES` - List of capability strings

2. For **HTTP agents** (preferred for complex agents), implement:
   - `GET /.well-known/agent.json` - Agent card endpoint
   - `GET /health` - Health check endpoint
   - `POST /` - Main request handler
   - Status protocol output to stdout (STATUS:running:message)

3. For **stdio agents** (simpler agents), implement:
   - `async invoke(self, request)` method
   - `get_agent_card()` method
   - `main()` function that reads JSON from stdin and writes to stdout

See `data/prompts/agent_structure.md` for complete HTTP endpoint specifications and code examples.

## Requirements
- Clear name and description
- List of capabilities (for registry discovery)
- Interface specification
- Dependencies needed
- Test cases to validate

## Response Format (JSON)
```json
{
    "name": "<component_name_snake_case>",
    "description": "<what it does>",
    "kind": "agent|mcp",
    "capabilities": ["<capability_1>", "<capability_2>"],
    "interface": {
        "inputs": [],
        "outputs": []
    },
    "transport": "http|stdio",
    "entrypoint": "app/agents/<agent_name>/agent.py",
    "dependencies": [],
    "implementation_notes": "<guidance for implementation>",
    "test_cases": [
        {"name": "<test_name>", "input": "<test_input>", "expected": "<expected_output>"}
    ]
}
```

## Transport Selection

- Use `http` transport for agents that:
  - Need to handle multiple concurrent requests
  - Require long-running processes
  - Need to maintain state between requests
  - Will be managed by the HTTPAgentManager

- Use `stdio` transport for agents that:
  - Are simple request-response style
  - Don't need persistent state
  - Are called occasionally
