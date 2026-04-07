# Greeting Ack Agent

Minimal A2A agent that acknowledges simple user greetings and prompts for next input.

## Behaviour

* Accepts `{"message": "...", "context": {...}}` payloads.
* If `message` (case-insensitive, trimmed) is a greeting (`hi`, `hello`, `hey`, `hiya`) it
  responds with a friendly acknowledgement and follow-up question.
* Logs the `work_item_id` (`b64cd775-2cc6-4ec9-8895-ab55fbccc6ca`) to `logs/greeting-ack.log`
  on every invocation.

## Usage

```python
from app.agents.greeting_ack import handle

result = handle({"message": "hi", "context": {}})
print(result["text"])  # "Hi there! What can I help you with next?"
```

## Configuration

| Environment variable      | Default          | Description              |
|---------------------------|------------------|--------------------------|
| `GREETING_ACK_LOG_DIR`    | `logs`           | Directory for log files  |
