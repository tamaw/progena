# Hello World Agent

A minimal demonstration agent that responds with `"Hello World!"` to any incoming request, regardless of content.

## Description

The Hello World Agent is a baseline reference implementation for validating agent loading, routing, and protocol compliance. It always returns the same static response to any input, making it useful for testing the agent infrastructure.

## Features

- **Stateless**: No state management or persistence
- **Deterministic**: Always returns `"Hello World!"`
- **Protocol Compliant**: Implements A2A (Agent-to-Agent) HTTP protocol
- **Simple**: Minimal dependencies and complexity

## How to Run

```bash
python app/agents/hello_world_agent/agent.py --port 8000
```

Or from the repository root:

```bash
python -m app.agents.hello_world_agent.agent --port 8000
```

## HTTP Endpoints

### Agent Card
```bash
curl http://localhost:8000/.well-known/agent.json
```

Response:
```json
{
  "name": "Hello World Agent",
  "description": "Responds with 'Hello World!' to any request.",
  "version": "0.1.0",
  "protocol": "a2a",
  "capabilities": ["hello_world", "static_response"],
  "endpoint": "http://localhost:8000"
}
```

### Health Check
```bash
curl http://localhost:8000/health
```

Response:
```json
{
  "status": "healthy",
  "name": "Hello World Agent",
  "version": "0.1.0",
  "uptime_seconds": 3.14159,
  "timestamp": "2026-01-31T05:03:57.576Z"
}
```

### Main Request Handler

```bash
curl -X POST http://localhost:8000/ \
  -H "Content-Type: application/json" \
  -d '{"request":"Anything at all"}'
```

Response:
```json
{
  "success": true,
  "response": "Hello World!",
  "error": null,
  "metadata": {}
}
```

Any request will always receive the same response:

```bash
curl -X POST http://localhost:8000/ \
  -H "Content-Type: application/json" \
  -d '{"request":"How are you?"}'
```

Still returns:
```json
{
  "success": true,
  "response": "Hello World!",
  "error": null,
  "metadata": {}
}
```

## Status Output

The agent emits status messages to STDOUT following the pattern:
```
STATUS:{state}:{message}
```

Example output:
```
STATUS:starting:Initializing Hello World Agent
STATUS:running:Server started on port 8000
STATUS:executing:Processing request
STATUS:waiting:Idle, waiting for requests
```

## Agent Metadata

- **Agent ID**: `hello_world_agent`
- **Agent Name**: `Hello World Agent`
- **Version**: `0.1.0`
- **Protocol**: A2A (HTTP)
- **Capabilities**: `hello_world`, `static_response`

## Requirements

- Python 3.8+
- aiohttp

## License

Part of the Progenitor project.
