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

The agent.py file MUST:
1. Define a main agent class with an `invoke()` async method
2. Include AGENT_ID, AGENT_NAME, DESCRIPTION, CAPABILITIES class attributes
3. Have a `get_agent_card()` method for registry discovery
4. Include a `main()` function for direct invocation (stdio transport)

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
    "transport": "stdio",
    "entrypoint": "app/agents/<agent_name>/agent.py",
    "dependencies": [],
    "implementation_notes": "<guidance for implementation>",
    "test_cases": [
        {"name": "<test_name>", "input": "<test_input>", "expected": "<expected_output>"}
    ]
}
```
