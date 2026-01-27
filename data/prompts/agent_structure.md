## Directory Structure (CRITICAL - MUST FOLLOW EXACTLY)

**DO NOT create files directly in app/agents/. ALL agent files MUST go in a subdirectory.**

Create the agent in the subdirectory `app/agents/{agent_name}/` with these EXACT paths:
- `app/agents/{agent_name}/__init__.py` - Export the main agent class
- `app/agents/{agent_name}/agent.py` - Main agent implementation (REQUIRED)
- `app/agents/{agent_name}/README.md` - Documentation

WRONG: `app/agents/{agent_name}_agent.py` or `app/agents/{agent_name}.py`
CORRECT: `app/agents/{agent_name}/agent.py`

The `app/agents/{agent_name}/agent.py` file MUST include:
1. Agent class with AGENT_ID, AGENT_NAME, DESCRIPTION, CAPABILITIES class attrs
2. async `invoke(self, request)` method as the main entry point
3. `get_agent_card()` method returning agent metadata dict
4. `main()` function for stdio transport (reads JSON from stdin, writes to stdout)

Example main() function:
```python
import sys
import json
import asyncio

def main():
    agent = {agent_class}Agent()
    input_data = json.loads(sys.stdin.read())
    result = asyncio.run(agent.invoke(input_data))
    print(json.dumps(result, default=str))

if __name__ == "__main__":
    main()
```
