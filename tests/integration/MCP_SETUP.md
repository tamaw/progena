# MCP Integration Setup

## Current Status

The integration test for MCP tools (`test_mcp_tool`) is currently **not functional** because MCP requires authentication that is not configured.

## Issue

Azure Responses's MCP endpoint at `http://localhost:4000/mcp/` returns `500 Internal Server Error` due to missing authentication:

```
Azure Responses/proxy/_experimental/mcp_server/auth/user_api_key_auth_mcp.py", line 120, in process_mcp_request
  validated_user_api_key_auth = await user_api_key_auth(...)
```

## Root Cause

In `docker/docker-compose.Azure Responses.yml`, `JINA_API_KEY` is set to an empty string:

```yaml
JINA_API_KEY: ""
```

The MCP endpoint requires authentication but has no keys configured.

## Configuration

`docker/Azure Responses/Azure Responses-config.yaml` has MCP configured:

```yaml
mcp_servers:
  jina_mcp:
    url: "https://mcp.jina.ai/v1"
    transport: "http"
    auth_type: "bearer_token"
    auth_value: os.environ/JINA_API_KEY
```

But the `JINA_API_KEY` environment variable is empty.

## To Fix

1. **Get a Jina API key** from https://jina.ai/
2. **Set the key in `docker/docker-compose.Azure Responses.yml`:**
   ```yaml
   JINA_API_KEY: "your-api-key-here"
   ```
3. **Set Progenitor MCP auth key (reuse Azure OpenAI key):**
   ```bash
   $env:PROGENITOR_MCP_API_KEY = $env:AZURE_OPENAI_API_KEY
   ```
4. **Restart Azure Responses:**
   ```bash
  docker compose -f docker/docker-compose.Azure Responses.yml restart Azure Responses
   ```
5. **Re-run the integration tests**

## Testing MCP Manually

Once configured, test the MCP endpoint:

```powershell
$headers = @{
    "Content-Type" = "application/json"
    "Authorization" = "Bearer $env:PROGENITOR_MCP_API_KEY"
}

$body = @{
    jsonrpc = "2.0"
    id = 1
    method = "tools/list"
    params = @{}
} | ConvertTo-Json

Invoke-RestMethod -Uri "http://localhost:4000/mcp/" `
    -Method Post `
    -Body $body `
    -Headers $headers
```

Expected response:
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "tools": [
      {
        "name": "search_web",
        "description": "Search the web using Jina AI",
        ...
      }
    ]
  }
}
```

## What the Test Does

The `test_mcp_tool` integration test:

1. Sends a message requesting a web search via MCP
2. Expects the agent to either:
   - Use Jina MCP `search_web` tool (if configured)
   - Respond that MCP is not available (current behavior)
3. Writes results to `tests/integration/results/mcp_tool.json`

## Alternative: Use A2A Agents Instead

If MCP setup is too complex, consider testing **A2A agents** instead, which don't require external API keys:

```python
message = "Tell me the current BTC price using the BTC Price Agent"
```

This uses the local `app/agents/BTC Price Agent/` which is already registered.

