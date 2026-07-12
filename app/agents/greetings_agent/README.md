# Greetings Agent

Minimal standalone A2A test agent for exercising Progenitor's A2A flow end to end.

It is intentionally isolated:

- Single file runtime in [agent.py](./agent.py)
- No imports from `app.progenitor` or other `app.agents.*`
- In-memory task store and push-notification config store

Features:

- Greets the caller using the same Azure OpenAI environment variables as the rest of the repo
- `sleep` tool to simulate async work and tracked polling
- `probe_copilot` tool to spawn GitHub Copilot CLI over ACP and ask it to say hello back
- `do_step` tool (called once per step) for multi-step tasks, to exercise live per-tool-call
  progress streaming (e.g. "do a 3-step task: prep, build, verify")
- Hermetic mock mode with `PROGENITOR_MOCK_LLM=true`
- Streaming (`message/stream` / `tasks/resubscribe`) is enabled on the agent card; each tool
  call and step is pushed as its own live `TaskStatusUpdateEvent`

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

Note: `execute()` runs the whole greeting to completion before returning, rather than handing off to a detached background task -- the SDK closes the event queue the instant `execute()` returns, so a detached task's events would be silently dropped. The A2A framework itself keeps a non-blocking `message/send` caller from waiting on that, by continuing to consume events in the background after handing back an early "submitted" response. Progenitor's normal behavior is still to poll `tasks/get` until terminal state, which continues to work; a caller that wants live progress can instead use `message/stream` or `tasks/resubscribe`.
