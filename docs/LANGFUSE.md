# Langfuse Observability Integration

Progenitor includes built-in support for [Langfuse](https://langfuse.com), an open-source LLM observability platform.

## Features

When enabled, Langfuse automatically captures:

- **All LLM calls** - prompts, completions, model parameters
- **Token usage** - input/output token counts
- **Cost tracking** - estimated costs based on model pricing
- **Latency** - response times for each call
- **Errors** - failed API calls with error messages
- **Session tracking** - group related traces together

## Quick Start

### 1. Start the Stack

The docker-compose file includes a complete self-hosted Langfuse stack:

```bash
docker compose up -d
```

This starts:
- **Langfuse Web** (http://localhost:3000) - Dashboard and API
- **Langfuse Worker** - Background job processor
- **PostgreSQL** - Primary database
- **ClickHouse** - Analytics database
- **Redis** - Cache and job queue
- **MinIO** - Object storage

### 2. Create an Account

1. Open http://localhost:3000
2. Sign up for an account
3. Create a new project

### 3. Get API Keys

1. Go to Settings > API Keys
2. Create a new API key pair
3. Copy the public and secret keys

### 4. Configure Environment

Add to your `.env` file:

```bash
LANGFUSE_ENABLED=true
LANGFUSE_PUBLIC_KEY=pk-lf-your-public-key
LANGFUSE_SECRET_KEY=sk-lf-your-secret-key
# For docker-compose, host is auto-configured
# For local dev: LANGFUSE_HOST=http://localhost:3000
```

### 5. Restart Progenitor

```bash
docker compose restart progenitor chat_server
```

## Viewing Traces

1. Open http://localhost:3000
2. Navigate to your project
3. Click on "Traces" in the sidebar
4. You'll see all LLM calls with full details

### Trace Details

Each trace includes:
- Input/output content
- Model used
- Token counts
- Latency
- Cost estimate
- Any errors

### Filtering & Search

Use Langfuse's powerful filtering to:
- Search by content
- Filter by model
- Filter by time range
- Filter by user/session
- Filter by tags

## Configuration Options

| Variable | Description | Default |
|----------|-------------|---------|
| `LANGFUSE_ENABLED` | Enable/disable integration | Auto-enabled if keys present |
| `LANGFUSE_PUBLIC_KEY` | Public API key | (required) |
| `LANGFUSE_SECRET_KEY` | Secret API key | (required) |
| `LANGFUSE_HOST` | Langfuse server URL | Docker: `http://langfuse-web:3000` |
| `LANGFUSE_DEBUG` | Enable debug logging | `false` |
| `LANGFUSE_SAMPLE_RATE` | Sampling rate (0.0-1.0) | `1.0` (all requests) |

## Using Langfuse Cloud

Instead of self-hosting, you can use [Langfuse Cloud](https://cloud.langfuse.com):

1. Sign up at https://cloud.langfuse.com
2. Create a project and get API keys
3. Update your `.env`:

```bash
LANGFUSE_HOST=https://cloud.langfuse.com  # or https://us.cloud.langfuse.com
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
```

## Advanced Usage

### Custom Trace Attributes

Add custom metadata to traces:

```python
from langfuse.openai import openai

response = openai.chat.completions.create(
    model="gpt-4",
    messages=[...],
    # Add Langfuse metadata
    metadata={
        "langfuse_session_id": "session_123",
        "langfuse_user_id": "user_456",
        "langfuse_tags": ["production", "important"],
        "custom_key": "custom_value"
    }
)
```

### Manual Tracing with Decorator

```python
from app.progenitor.observability import observe

@observe(name="my-function", session_id="session_123")
async def my_function():
    # Your code here
    pass
```

### Flushing on Exit

Langfuse batches events for efficiency. For short-lived scripts, ensure traces are sent:

```python
from app.progenitor.observability import flush_langfuse

# At end of script
flush_langfuse()
```

## Troubleshooting

### Traces Not Appearing

1. Check Langfuse is enabled:
   ```python
   from app.progenitor.observability import get_langfuse_config
   print(get_langfuse_config().is_configured())
   ```

2. Enable debug mode:
   ```bash
   export LANGFUSE_DEBUG=true
   ```

3. Verify connectivity:
   ```bash
   curl http://localhost:3000/api/public/health
   ```

### Container Issues

Check logs:
```bash
docker compose logs langfuse-web
docker compose logs langfuse-worker
```

### Reset Langfuse Data

To clear all data and start fresh:
```bash
docker compose down -v  # Removes volumes
docker compose up -d
```

## Architecture

```
┌─────────────────────┐     ┌─────────────────────┐
│    Progenitor       │────▶│    Langfuse Web     │
│    Chat Server      │     │    (port 3000)      │
└─────────────────────┘     └──────────┬──────────┘
                                       │
                                       ▼
┌─────────────────────┐     ┌─────────────────────┐
│   Langfuse Worker   │◀───▶│       Redis         │
└─────────────────────┘     └─────────────────────┘
         │
         ▼
┌─────────────────────┐     ┌─────────────────────┐
│    PostgreSQL       │     │     ClickHouse      │
│   (transactional)   │     │    (analytics)      │
└─────────────────────┘     └─────────────────────┘
         │
         ▼
┌─────────────────────┐
│       MinIO         │
│   (object storage)  │
└─────────────────────┘
```

## Resources

- [Langfuse Documentation](https://langfuse.com/docs)
- [Langfuse GitHub](https://github.com/langfuse/langfuse)
- [Self-hosting Guide](https://langfuse.com/self-hosting)
- [Python SDK Reference](https://langfuse.com/docs/sdk/python)
