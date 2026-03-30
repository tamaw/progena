# Observe Agent

Monitoring A2A agent that reads mounted Progenitor state and logs in read-only fashion and reports diagnostic status.

Expected mounts:

- State: `/app/data/state`
- Logs: `/app/logs`
- Prompts: `/app/prompts`
- Worktrees: `/app/worktrees`

Local build/run example:

```bash
docker build -t observe-agent -f app/agents/observe_agent/Dockerfile .
docker run --rm -p 8080:8080 \
  -e AGENT_NAME=observe_agent \
  -e AZURE_OPENAI_BASE_URL=... \
  -e AZURE_OPENAI_API_KEY=... \
  -v "$(pwd)/data:/app/data" \
  -v "$(pwd)/logs:/app/logs" \
  -v "$(pwd)/prompts:/app/prompts" \
  -v "$(pwd)/worktrees:/app/worktrees" \
  observe-agent
```
