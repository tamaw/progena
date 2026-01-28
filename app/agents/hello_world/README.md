# New Agent

A basic agent-to-agent (A2A) template for the Progenitor system.

## Overview

This is a starter template for building A2A agents within Progenitor. It demonstrates:
- Standard JSON request/response format
- Message processing pipeline
- Error handling
- Integration with Progenitor's agent registry

## Usage

### Direct Execution

```bash
python app/agents/new_agent/agent.py < input.json > output.json
```

### Via Progenitor

Once registered in `data/state/registry.json`, this agent can be invoked through Progenitor's A2A delegation system.

## Request Format

```json
{
  "id": "unique-request-id",
  "message": "Your message here",
  "metadata": {
    "key": "value"
  }
}
```

## Response Format

```json
{
  "id": "unique-request-id",
  "message": "Response text",
  "status": "success",
  "error": null
}
```

## Development

- Modify `process_message()` to implement your agent logic
- Add capabilities and features as needed
- Update the registry entry in `data/state/registry.json` to reflect capabilities

## Version

0.1.0
