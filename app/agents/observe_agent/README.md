# Observe Agent

Monitoring A2A agent that reads mounted Progenitor state and logs in read-only fashion and reports diagnostic status.

Expected mounts:

- State: `/app/data/state`
- Logs: `/app/logs`
- Prompts: `/app/prompts`
- Worktrees: `/app/worktrees`

Local build/run example:

```bash
docker build -t observe-agent -f docker/agents/Dockerfile \
  --build-arg AGENT_NAME=observe_agent \
  --build-arg AGENT_PORT=8080 .
docker run --rm -p 8080:8080 \
  -e AGENT_NAME=observe_agent \
  -e AGENT_PORT=8080 \
  -e AZURE_OPENAI_BASE_URL=... \
  -e AZURE_OPENAI_API_KEY=... \
  -v "$(pwd)/data:/app/data" \
  -v "$(pwd)/logs:/app/logs" \
  -v "$(pwd)/data/prompts:/app/data/prompts:ro" \
  -v "$(pwd)/data/skills:/app/data/skills:ro" \
  -v "$(pwd)/worktrees:/app/worktrees" \
  observe-agent
```
