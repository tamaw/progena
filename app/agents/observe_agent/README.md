# Observe Agent

Monitoring A2A agent that reads Progenitor's mounted state + logs (read-only) and reports status.

Expected mounts (defaults):
- State: `/app/data/state` (read-only)
- Logs: `/app/logs` (read-only)

Local build/run example:
```bash
docker build -t observe-agent -f app/agents/observe_agent/Dockerfile app/agents/observe_agent
docker run --rm -p 8080:8080 \
  -v "$(pwd)/data/state:/app/data/state:ro" \
  -v "$(pwd)/logs:/app/logs:ro" \
  observe-agent
```


