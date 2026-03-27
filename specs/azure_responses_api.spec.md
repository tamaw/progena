# Azure OpenAI Responses API — Integration Spec

Covers the non-obvious differences between the Azure `/responses` endpoint and
the standard Chat Completions API. Assumes `langchain_openai` / `httpx` are
already in use. Focus is on what breaks silently if you get it wrong.

---

## Request — field name changes

These fields are renamed vs Chat Completions:

- `messages` → `input`
- `max_tokens` → `max_output_tokens`
- `response_format` → **does not exist** — use `text.format` instead (see Structured Output)

Valid `input` roles: `system`, `user`, `assistant`, `tool`. Unknown roles must be coerced (e.g. to `user`) before sending.

Auth uses `api-key` header, not `Authorization: Bearer`:
```python
headers = {"Content-Type": "application/json", "api-key": "<AZURE_OPENAI_API_KEY>"}
```

Some models (e.g. `gpt-5.2-chat`) reject `temperature`. Omit it or retry without it on a 400 whose error message contains `"temperature"` and `"unsupported"`.

---

## Streaming (LangChain)

Pass these three params to `ChatOpenAI`:

```python
ChatOpenAI(
    use_responses_api=True,        # POST to /responses, parse Responses v1 shape
    output_version="responses/v1", # tells langchain_openai which output schema to expect
    streaming=True,                # consume SSE; prevents read timeouts on long reasoning
    default_headers={"api-key": api_key},
)
```

- Strip `/responses` from the base URL before passing to `ChatOpenAI` — it appends `/responses` itself.
- `streaming=True` is recommended even when calling `ainvoke()` (not `astream()`): it lowers TTFT and avoids HTTP read timeouts on reasoning-heavy calls.
- For reasoning-capable models pass `reasoning_effort="low"|"medium"|"high"` to `ChatOpenAI`. The library translates this to `{"reasoning": {"effort": "<level>"}}` in the payload.

SSE event shape from Azure:
```
data: {"type": "response.output_item.delta", "delta": {"type": "text", "text": "..."}}
data: {"type": "response.output_item.delta", "delta": {"type": "tool_call", ...}}
data: {"type": "response.completed", "response": { <full response object> }}
data: [DONE]
```

---

## Structured Output

**The `response_format` field does not exist on `/responses`.** Using it returns `400 Invalid request`.

Use `text.format` instead, with the schema fields promoted one level up (the `json_schema` wrapper is removed):

```json
{
  "text": {
    "format": {
      "type":   "json_schema",
      "name":   "my_schema",
      "schema": { ... },
      "strict": true
    }
  }
}
```

### Schema preprocessing for `strict: true`

Azure strict mode rejects schemas that aren't fully closed. Pydantic's `model_json_schema()` doesn't produce these by default — you must fix the schema before sending:

- Every `"type": "object"` node needs `"additionalProperties": false` and `"required": [<all property keys>]`.
- `$ref` nodes must have **no sibling keys** — Azure rejects `{"$ref": "...", "description": "..."}`. Strip all siblings, keeping only `$ref`.
- Apply these fixes recursively to the entire schema including `$defs`.

```python
def close_object_schemas(node):
    if isinstance(node, dict):
        if "$ref" in node:
            ref = node["$ref"]
            node.clear()
            node["$ref"] = ref
            return
        if node.get("type") == "object":
            node["additionalProperties"] = False
            if props := node.get("properties"):
                node["required"] = list(props.keys())
        for v in node.values():
            close_object_schemas(v)
    elif isinstance(node, list):
        for item in node:
            close_object_schemas(item)
```

### Full pipeline

```python
schema = MyModel.model_json_schema()
close_object_schemas(schema)                           # fix strict constraints
if "properties" in schema:
    schema["required"] = list(schema["properties"].keys())

payload["text"] = {
    "format": {
        "type":   "json_schema",
        "name":   "my_schema",
        "schema": schema,
        "strict": True,
    }
}
```

### Structured output + tools

When `text.format` is set, also set `tool_choice: "none"` to prevent the model from emitting tool calls instead of the JSON object. The tools list can still be included in the payload for model awareness.

### Extracting the result

The structured JSON comes back as a plain text string in an `output_text` content block — **not** a pre-parsed object. Parse it with `json.loads()` then validate with your model.

---

## Reasoning

Request a reasoning summary by adding to the payload:
```json
"reasoning": {"summary": "auto"}
```

The model runs chain-of-thought internally and returns a compressed summary (not the raw token stream). Omit this field if you don't need it.

### Where reasoning appears in the response

Azure may put reasoning in any of three locations — handle all of them:

**A) Top-level `output[]` item:**
```json
{"type": "reasoning", "summary": [{"type": "summary_text", "text": "..."}]}
```

**B) Inside a message's `content[]`:**
```json
{"type": "message", "content": [
  {"type": "reasoning", "text": "..."},
  {"type": "output_text", "text": "Final answer"}
]}
```

**C) Top-level `reasoning` key:**
```json
{"reasoning": {"summary": "..."}, "output": [...]}
```

---

## Response Shape Reference

Top-level response object:
```json
{
  "id": "resp_abc123",
  "object": "response",
  "model": "...",
  "status": "completed",
  "output": [...],
  "usage": {"input_tokens": 120, "output_tokens": 45, "total_tokens": 165},
  "incomplete_details": null,
  "content_filters": []
}
```

### `output[]` item types

| `type` | Key fields |
|---|---|
| `"message"` | `role`, `content: [{type, text}]` |
| `"reasoning"` | `summary: [{type: "summary_text", text}]` |
| `"tool_call"` | `call_id`, `name`, `arguments` (object or JSON string) |
| `"function_call"` | `id`, `function.name`, `function.arguments` (JSON string) |
| `"output_text"` | `text` |
| `"refusal"` | `refusal` or `text` |

### Message `content[]` part types

`output_text`, `text`, `reasoning`, `reasoning_text`, `reasoning_summary`, `summary_text`, `refusal`, `tool_call`

### Text extraction waterfall

When parsing a response, check in this order to collect text:
1. `data["output_text"]` (top-level shorthand)
2. `data["text"]` (top-level shorthand)
3. `data["output"][]` → items of type `message` → `content[]` → parts of type `output_text` or `text`
4. `data["output"][]` → items of type `output_text` or `text`

When response text is empty, inspect `data["status"]`, `data["incomplete_details"]` (e.g. `{"reason": "max_output_tokens"}`), and `data["content_filters"]` to diagnose why.

---

## Tool Calling

Tools use standard OpenAI function format (`type: "function"`, `function.name`, `function.parameters`). Tool parameters should also have `additionalProperties: false` and a full `required` list.

### Tool call response shapes

Azure may put tool calls in any of three locations:
- `data["output"][]` — items with `type: "tool_call"` or `"function_call"`
- `data["output"][message]["content"][]` — parts with `type: "tool_call"`
- `data["tool_calls"][]` — direct list (legacy fallback)

Normalise both call shapes. Field extraction priority:
- Name: `name` → `tool_name` → `function.name`
- Args: `arguments` → `input` → `params` → `function.arguments` (JSON-decode if string)
- Call ID: `call_id` → `id` → `tool_call_id`

### Multi-round tool loop

```
POST /responses
  ↓ tool_calls present?
  → execute each tool
  → append results as user messages: "[TOOL_RESULT tool=<name> id=<call_id>] <result_json>"
  → POST /responses again with updated input[]
  → repeat until no tool_calls or round limit reached
```

- Inject results as `role: "user"` plain-text messages for broadest model compatibility (the `tool` role also works but some models handle plain-text injection more reliably).
- Guard against infinite loops: track a `"<tool_name>:<sorted_args_json>"` signature per call; return an error result without re-executing if the same call is seen twice.
- Cap rounds (e.g. 4); log a warning and return the last response if the cap is hit.

---

## Gotcha Summary

| Mistake | Symptom |
|---|---|
| Using `response_format` at top level | `400 Invalid request` |
| Schema objects missing `additionalProperties: false` | `400` or model ignores strict mode |
| Schema `$ref` nodes with sibling keys | `400` strict schema validation error |
| `temperature` sent to unsupported model | `400`; retry without it |
| Not checking all three reasoning locations | Missing reasoning trace silently |
| Not checking all three tool call locations | Missed tool calls |
| Tool args arriving as JSON string not object | `TypeError` on dict access; always `json.loads` if string |
