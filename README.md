# Progena AI

[Progena AI](https://progena.ai) is a self-hosted conversational AI agent system. Chat with an agent, connect tools, automate work with workflows and schedules, and create interactive canvases—all through a web interface.

## Features

- Persistent conversations and PostgreSQL-backed agent state.
- Tools and integrations through MCP, A2A and ACP.
- Workflows and scheduled tasks.
- Agent-built interactive canvases.
- Configurable model providers and profiles, including Microsoft Foundry and OpenCode Go via the Responses API.

Built with Python, FastAPI, LangGraph and React.

## Getting started

You'll need Docker with Compose, or Python, Node.js and PostgreSQL with pgvector for local development. Configure a supported model provider to run agent tasks.

- [Docker setup](docs/DOCKER.md)
- [Local development](docs/LOCAL_STARTUP.md)
- [Architecture](docs/ARCHITECTURE_DIAGRAM.md)
- [API documentation](docs/BACKEND.md)

Progena is under active development. Keep deployments private or behind an authenticated reverse proxy; agents with shell and tool access should run only in environments you trust.

Standalone A2A agents are maintained separately in [progena.agents](https://github.com/tamaw/progena.agents), currently a private repository.

## License

[MIT](LICENSE)
