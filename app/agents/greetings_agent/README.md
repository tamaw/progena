# Greetings Agent

Minimal standalone A2A test agent for exercising Progenitor's A2A flow end to end.

It is intentionally isolated:

- Single file runtime in [agent.py](/home/tama/source/progenitor/main/app/agents/greetings_agent/agent.py)
- No imports from `app.progenitor` or other `app.agents.*`
- In-memory task store and push-notification config store

Features:

- Greets the caller using the same Azure OpenAI environment variables as the rest of the repo
- `sleep` tool to simulate async work and tracked polling
- `probe_copilot` tool to spawn GitHub Copilot CLI over ACP and ask it to say hello back
- Hermetic mock mode with `PROGENITOR_MOCK_LLM=true`

Environment:

- `PORT` defaults to `8109`
- `HOST` defaults to `127.0.0.1`
- `A2A_PUBLIC_HOST` defaults to `localhost`
- `AZURE_OPENAI_BASE_URL`
- `AZURE_OPENAI_API_KEY`
- `AGENT_MODEL_DEFAULT` or `PROGENITOR_MODEL_DEFAULT`
- `PROGENITOR_COPILOT_BIN` defaults to `copilot`
- `DEFAULT_COPILOT_MODEL` defaults to `gpt-5-mini`
- `PROGENITOR_MOCK_LLM=true` for deterministic no-LLM behavior

Run:

```bash
source .venv/bin/activate
PROGENITOR_MOCK_LLM=true PORT=8109 python -m uvicorn app.agents.greetings_agent.agent:app --host 127.0.0.1 --port 8109
```

Health and card:

```bash
curl http://127.0.0.1:8109/health
curl http://127.0.0.1:8109/.well-known/agent-card.json | jq .
```

Sample non-blocking `message/send`:

```bash
curl -s http://127.0.0.1:8109/ \
  -H 'content-type: application/json' \
  -d '{
    "jsonrpc": "2.0",
    "id": "1",
    "method": "message/send",
    "params": {
      "message": {
        "messageId": "msg-1",
        "role": "user",
        "parts": [{"type": "text", "text": "sleep 3 then say hi"}]
      },
      "configuration": {"blocking": false}
    }
  }'
```

Then poll the returned task id:

```bash
curl -s http://127.0.0.1:8109/ \
  -H 'content-type: application/json' \
  -d '{
    "jsonrpc": "2.0",
    "id": "2",
    "method": "tasks/get",
    "params": {"id": "TASK_ID_HERE"}
  }'
```

Registration example from Progenitor:

```text
register_a2a_agent(name="greetings_agent", url="http://localhost:8109")
```

Note: `blocking=true` still returns the submitted snapshot. That is deliberate: the SDK closes the event queue as soon as `execute()` returns, so this agent treats the task store as the source of truth and finishes work from a background task. Progenitor's normal behavior is to poll `tasks/get` until terminal state anyway.
