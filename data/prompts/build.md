# Build Specification Prompt

Generate a detailed specification for building a {item_type}.

## NAME EXTRACTION (CRITICAL)
Extract a descriptive snake_case name from the description. Examples:
- "hello world agent" → name: "hello_world"
- "email notification agent" → name: "email_notifier"
- "weather lookup agent" → name: "weather_lookup"
- "calculator tool" → name: "calculator"
- "file converter tool" → name: "file_converter"

NEVER use "unnamed" as the name - always derive from the description.

## Build Discipline
- Keep diffs minimal
- Update tests if behaviour changes
- Follow Progenitor framework patterns
- Make it registrable in registry.json

## Directory Structure for Agents (A2A)
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

## Directory Structure for Tools (FastMCP)
Tools MUST be created in a subdirectory under `app/mcp/<tool_name>/`:

CORRECT structure:
```
app/mcp/<tool_name>/
├── __init__.py          # Exports the mcp server and tool functions
├── server.py            # FastMCP server implementation (REQUIRED)
├── config.py            # Tool configuration (optional)
└── tests/
    └── test_<tool_name>.py  # Pytest tests
```

## Agent Implementation Requirements (A2A)

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

## Tool Implementation Requirements (FastMCP)

Each tool uses FastMCP. The server.py file MUST:

1. Initialize FastMCP with the tool name:
   ```python
   from mcp.server.fastmcp import FastMCP
   mcp = FastMCP("<tool_name>")
   ```

2. Define tool functions with @mcp.tool() decorator:
   ```python
   @mcp.tool()
   async def my_function(param1: str, param2: int = 10) -> str:
       """Description of the tool function."""
       return result
   ```

3. Include a main entry point:
   ```python
   def main():
       mcp.run()

   if __name__ == "__main__":
       main()
   ```

See `data/prompts/tool_structure.md` for complete FastMCP specifications and code examples.

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
    "kind": "agent|tool",
    "capabilities": ["<capability_1>", "<capability_2>"],
    "interface": {
        "inputs": [],
        "outputs": []
    },
    "transport": "http|stdio",
    "entrypoint": "app/agents/<agent_name>/agent.py | app/mcp/<tool_name>/server.py",
    "dependencies": [],
    "implementation_notes": "<guidance for implementation>",
    "test_cases": [
        {"name": "<test_name>", "input": "<test_input>", "expected": "<expected_output>"}
    ]
}
```

## Transport Selection

**For Agents (A2A):**
- Use `http` transport for agents that:
  - Need to handle multiple concurrent requests
  - Require long-running processes
  - Need to maintain state between requests
  - Will be managed by the HTTPAgentManager

- Use `stdio` transport for agents that:
  - Are simple request-response style
  - Don't need persistent state
  - Are called occasionally

**For Tools (FastMCP):**
- Always use `stdio` transport (FastMCP default)
- Tools are stateless by design
- Called via MCP protocol
