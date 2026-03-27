# Azure OpenAI Responses API — Integration Specification

> Documents how this codebase calls the Azure OpenAI `/responses` endpoint:
> request shape, streaming, tool calling, reasoning, and the exact structured-output
> transformations that make strict JSON mode work.  References are to the actual
> source files so every claim can be verified.

---

## 1. Two API Client Paths

The codebase uses the Azure `/responses` endpoint through **two distinct paths**.
Both call the same Azure endpoint but differ in how they build requests, parse
responses, and handle streaming.

| Path | File | Used by | Streaming |
|---|---|---|---|
| **Direct `httpx`** | `app/agents/builder_progenitor/client.py` (`BuilderClient`) | Builder Progenitor planner | No — single blocking call |
| **Direct `httpx`** | `app/agents/builder_agent/llm/client.py` (`LLMClient`) | Builder Agent inner loop | No — single blocking call with optional tool loop |
| **LangChain `ChatOpenAI`** | `app/agents/builder_progenitor/agent.py` (`_create_model`) | Builder Progenitor execution graph | Yes — SSE via `use_responses_api=True` |
| **LangChain `ChatOpenAI`** | `app/progenitor/agent/langchain_adapter.py` (`create_langchain_model`) | Progenitor main loop | Yes — SSE via `use_responses_api=True` |

The direct-`httpx` path owns request construction and response parsing completely.
The LangChain path delegates both to `langchain_openai`'s built-in Responses API
support; only the `ChatOpenAI` constructor parameters matter to this codebase.

---

## 2. Endpoint and Authentication

### 2.1 URL Construction

Both client implementations call a helper that normalises `AZURE_OPENAI_BASE_URL`
into a valid `/responses` endpoint:

```python
# builder_progenitor/client.py  _build_responses_endpoint()
# builder_agent/llm/client.py   _build_responses_endpoint()  (identical logic)
raw = (base_url or "").strip()
parsed = urlsplit(raw)
path = (parsed.path or "").rstrip("/")
endpoint_path = path if path.endswith("/responses") else f"{path}/responses"
return urlunsplit((parsed.scheme, parsed.netloc, endpoint_path, ...))
```

`AZURE_OPENAI_BASE_URL` may be either:
- `https://<resource>.openai.azure.com/openai/v1` (no suffix) → `/responses` is appended
- `https://<resource>.openai.azure.com/openai/v1/responses` (with suffix) → used as-is

The LangChain path strips the `/responses` suffix before passing to `ChatOpenAI`
because LangChain appends `/responses` itself when `use_responses_api=True`:

```python
# builder_progenitor/agent.py  _create_model()
# langchain_adapter.py         _normalize_langchain_base_url()
if path.endswith("/responses"):
    path = path[: -len("/responses")]
base_url = urlunsplit(...)
```

### 2.2 Authentication

All calls use the Azure API key header, not an `Authorization: Bearer` token:

```python
headers = {
    "Content-Type": "application/json",
    "api-key": api_key,          # from AZURE_OPENAI_API_KEY
}
```

For the LangChain path this is injected via `default_headers`:

```python
ChatOpenAI(
    api_key=SecretStr(api_key or "not-needed"),  # kept for library compat
    default_headers={"api-key": api_key or ""},  # the actual auth header
    ...
)
```

---

## 3. Request Payload Shape

The Azure Responses API differs from Chat Completions in several key fields.
This section describes the payload as constructed by the direct-`httpx` clients.

### 3.1 Field Mapping vs Chat Completions

| Chat Completions field | Responses API equivalent | Notes |
|---|---|---|
| `messages` | `input` | List of role/content dicts (same roles) |
| `max_tokens` | `max_output_tokens` | Renamed |
| `response_format` | `text.format` | Nested under `text` object; schema shape differs (§5) |
| `stream` | — | Not set by direct clients; always blocking |
| — | `reasoning` | New field for reasoning trace (§6) |
| `tool_choice` | `tool_choice` | Same field name, same values |
| `tools` | `tools` | Same field name and schema |

### 3.2 Minimal Request (no tools, no structured output, no reasoning)

```json
{
  "model": "gpt-5.2-chat",
  "input": [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user",   "content": "Hello"}
  ],
  "max_output_tokens": 4096,
  "temperature": 0.7
}
```

Code reference: `BuilderClient._build_payload()` / `LLMClient._post_response()`.

### 3.3 Input Message Construction

`_build_input_messages()` accepts a list of `{"role": ..., "content": ...}` dicts and:

1. Optionally prepends a `{"role": "system", "content": system_prompt}` item.
2. Coerces unknown roles (e.g. `"scheduler"`) to `"user"` — Azure only accepts
   `system | user | assistant | tool`.
3. Passes through list content unchanged **if** the first element's `type` is
   one of the native Responses API content block types:
   `input_text`, `output_text`, `input_image`, `input_file`, `refusal`,
   `summary_text`, `tether_browsing_display`, `computer_screenshot`, `tool_call`,
   `tool_result`.
4. Otherwise flattens list content to a single string by joining `text` keys.
5. Scalar content is coerced to `str`.

### 3.4 Temperature Guard

Some Azure model deployments reject `temperature` (e.g. models matching
`"gpt-5.2-chat"` in their name). The client checks `_supports_temperature()`
before including the field and retries without it on `400` responses whose error
message contains both `"temperature"` and `"unsupported"`:

```python
# LLMClient._post_response()
if self._supports_temperature():
    payload["temperature"] = temperature

try:
    response = await self.client.post(...)
    response.raise_for_status()
except httpx.HTTPStatusError as exc:
    if self._is_temperature_unsupported(exc.response):
        payload.pop("temperature", None)
        response = await self.client.post(...)   # retry
```

`BuilderClient` skips temperature entirely when `temperature is None` (its
default), making this issue moot for the planner path.

---

## 4. Streaming (LangChain Path)

### 4.1 How It Is Enabled

The LangChain-based graph clients (`builder_progenitor` execution graph and
Progenitor main loop) pass three constructor parameters to `ChatOpenAI`:

```python
ChatOpenAI(
    use_responses_api=True,       # Route to /responses not /chat/completions
    output_version="responses/v1",# Tell langchain_openai to parse Responses v1 output
    streaming=True,               # Consume the response as SSE
    ...
)
```

`use_responses_api=True` tells `langchain_openai` to POST to `/responses` and
parse the response shape described in §7.  
`streaming=True` instructs the library to open an SSE connection and consume
incremental `text.delta` events rather than waiting for a single JSON body.

### 4.2 What Streaming Means Here

Despite `streaming=True`, the `DeepAgent` graph always calls `ainvoke()` not
`astream()`.  From the caller's perspective the call blocks until the full
response is assembled; streaming is purely an implementation detail that:

- Allows the first tokens to arrive progressively (lower time-to-first-token),
- Enables the model to generate tool call arguments incrementally, and
- Prevents the HTTP client from hitting a read timeout on long reasoning traces.

The A2A `AgentCapabilities(streaming=False)` declaration means callers receive no
partial output — the entire response is emitted as a single `submitted` status
event. See `builder_progenitor.spec.md §6.1`.

### 4.3 Streaming Event Shape (SSE from Azure)

Azure sends `Content-Type: text/event-stream` with events of the form:

```
data: {"type": "response.output_item.delta", "delta": {"type": "text", "text": "Hello"}}
data: {"type": "response.output_item.delta", "delta": {"type": "tool_call", ...}}
data: {"type": "response.completed", "response": { /* full response object */ }}
data: [DONE]
```

`langchain_openai` assembles these into a complete response object before returning
it through the standard `ChatOpenAI` interface.  The direct-`httpx` clients do not
use SSE; they receive the entire response body at once.

### 4.4 Reasoning Effort (Progenitor Adapter)

The Progenitor adapter (`create_langchain_model`) optionally maps a configured
reasoning budget to a `reasoning_effort` level:

```python
if config.reasoning_enabled:
    if budget_tokens <= 1024:   reasoning_effort = "low"
    elif budget_tokens <= 4096: reasoning_effort = "medium"
    else:                       reasoning_effort = "high"

ChatOpenAI(..., reasoning_effort=reasoning_effort)
```

`langchain_openai` injects this as `{"reasoning": {"effort": "<level>"}}` in the
request payload. This is the LangChain equivalent of the direct client's
`payload["reasoning"] = {"summary": "auto"}` (see §6).

---

## 5. Structured Output

### 5.1 The Problem: Two Incompatible JSON Schema Shapes

The standard OpenAI Chat Completions `response_format` for structured output is:

```json
{
  "response_format": {
    "type": "json_schema",
    "json_schema": {
      "name": "my_schema",
      "schema": { ... },
      "strict": true
    }
  }
}
```

The Azure Responses API requires a **different shape** nested under `text`:

```json
{
  "text": {
    "format": {
      "type": "json_schema",
      "name": "my_schema",
      "schema": { ... },
      "strict": true
    }
  }
}
```

Key differences:
- Field is `text.format`, not top-level `response_format`
- The `json_schema` wrapper object is removed — `name`, `schema`, and `strict`
  are promoted one level up

### 5.2 `_to_azure_text_format()` — The Normalisation Step

Both `BuilderClient` and `LLMClient` implement this identical translation:

```python
@staticmethod
def _to_azure_text_format(response_format: dict) -> dict:
    if response_format.get("type") != "json_schema":
        return response_format           # pass through non-schema formats unchanged

    json_schema = response_format.get("json_schema")
    if not isinstance(json_schema, dict):
        return response_format

    return {
        "type": "json_schema",
        "name":   json_schema.get("name")   or "structured_output",
        "schema": json_schema.get("schema") or {},
        "strict": bool(json_schema.get("strict", True)),
    }
```

This is called in `_build_payload()`:

```python
if response_format is not None:
    payload["text"] = {"format": _to_azure_text_format(response_format)}
```

**Do not put `response_format` at the top level of the payload** — Azure will
return a `400 Invalid request` if you do.

### 5.3 `_close_object_schemas()` — Strict Mode Prerequisite

Azure's `strict: true` mode requires that **every object** in the JSON Schema
satisfies:
- `"additionalProperties": false`
- Every property key listed in `"required"`

Pydantic's `model_json_schema()` does not emit these by default. The helper
`_close_object_schemas()` walks the schema recursively and adds them:

```python
def _close_object_schemas(node):
    if isinstance(node, dict):
        if "$ref" in node:
            # $ref nodes must not have sibling keys — clear and restore only $ref
            ref = node["$ref"]
            node.clear()
            node["$ref"] = ref
            return
        if node.get("type") == "object":
            node["additionalProperties"] = False
            properties = node.get("properties")
            if isinstance(properties, dict):
                node["required"] = list(properties.keys())
        for value in node.values():
            _close_object_schemas(value)
    if isinstance(node, list):
        for item in node:
            _close_object_schemas(item)
```

**Why `$ref` nodes must be cleared:** A JSON Schema `$ref` is only valid if it
is the sole key in its object. Azure's strict validator rejects objects that have
`$ref` alongside sibling keys like `description`. The guard strips siblings while
preserving the reference.

**Why all properties are required:** `strict: true` does not allow optional
properties. Every property the Pydantic model defines must appear in the JSON
Schema's `required` list. Fields with `default` values are still required in
the output but the default is used for validation, not as a schema omission.

### 5.4 Full Schema Build Pipeline

```python
# Step 1 — Generate raw Pydantic schema
schema = response_model.model_json_schema()

# Step 2 — Enforce strict object constraints throughout
_close_object_schemas(schema)

# Step 3 — Ensure top-level required list matches properties
if "properties" in schema:
    schema["required"] = list(schema["properties"].keys())

# Step 4 — Wrap in OpenAI json_schema envelope (standard shape)
response_format = {
    "type": "json_schema",
    "json_schema": {
        "name":   schema_name,
        "schema": schema,
        "strict": True,
    },
}

# Step 5 — Flatten to Azure Responses shape and place under text.format
payload["text"] = {"format": _to_azure_text_format(response_format)}
```

### 5.5 Structured Output and Tool Calls

When a request includes both `tools` and `text.format` (structured output):

```python
# LLMClient._post_response()
if tool_choice is not None:
    payload["tool_choice"] = tool_choice
elif response_format is not None:
    payload["tool_choice"] = "none"   # ← prevent tool calls during structured output
else:
    payload["tool_choice"] = "auto"
```

Setting `tool_choice: "none"` tells Azure not to emit tool calls for this request,
even though the tool list is still sent. This preserves the model's tool-call
context (provider may use the list for function awareness) while ensuring the
response is a valid JSON object matching the schema, not a mix of text and tool
calls.

### 5.6 Extracting the JSON String

The structured JSON arrives as a plain text string inside the `output_text`
content block — not as a pre-parsed object. Extraction and parsing:

```python
# BuilderClient.chat_structured_with_reasoning()
response_text, reasoning = self._extract_text_and_reasoning(data)
parsed = response_model.model_validate(json.loads(response_text))
```

If `response_text` is empty after extraction the client checks `data["status"]`,
`data["incomplete_details"]`, and `data["content_filters"]` to produce a
diagnostic error message.

---

## 6. Reasoning

### 6.1 Requesting a Reasoning Trace

```python
# In _build_payload() / _post_response()
payload["reasoning"] = {"summary": "auto"}
```

`"summary": "auto"` instructs Azure to include a reasoning summary in the
response. The model performs internal chain-of-thought and returns a compressed
summary (not the raw token stream).

Only `BuilderClient` (`builder_progenitor` planner) and `LLMClient` with
`include_reasoning=True` include this field. The LangChain path uses
`reasoning_effort` instead (passed as a `ChatOpenAI` constructor parameter).

### 6.2 Reasoning Response Shape

Azure may return reasoning in any of three locations in the response:

**Location A — Top-level `output[]` item of type `"reasoning"`:**

```json
{
  "output": [
    {
      "type": "reasoning",
      "summary": [
        {"type": "summary_text", "text": "I need to consider..."}
      ]
    },
    {
      "type": "message",
      "content": [{"type": "output_text", "text": "Final answer"}]
    }
  ]
}
```

**Location B — Inside a message content part of type `"reasoning"` or `"summary_text"`:**

```json
{
  "output": [{
    "type": "message",
    "content": [
      {"type": "reasoning",    "text": "I need to consider..."},
      {"type": "output_text",  "text": "Final answer"}
    ]
  }]
}
```

**Location C — Top-level `reasoning` dict (less common):**

```json
{
  "reasoning": {"summary": "I need to consider..."},
  "output": [...]
}
```

### 6.3 `_extract_text_and_reasoning()` — Extraction Waterfall

Both clients implement the same extraction logic, iterating through all three
locations in priority order:

```python
def _extract_text_and_reasoning(data):
    text_parts = []
    reasoning_parts = []

    # --- Text extraction ---

    # Fast path: top-level shorthand
    if output_text := data.get("output_text"):
        text_parts.append(output_text)

    # Some providers use top-level "text"
    if top_text := data.get("text"):
        text_parts.append(top_text)

    for item in data.get("output", []):
        item_type = item.get("type")

        if item_type == "message":
            for part in item.get("content", []):
                part_type = part.get("type")
                if part_type in ("output_text", "text"):
                    text_parts.append(part.get("text") or part.get("value"))
                elif part_type == "refusal":
                    text_parts.append(part.get("refusal") or part.get("text"))
                elif part_type in ("reasoning", "reasoning_text", "reasoning_summary"):
                    reasoning_parts.append(part.get("text", ""))

        elif item_type in ("output_text", "text"):
            text_parts.append(item.get("text") or item.get("value"))

        elif item_type in ("reasoning", "reasoning_text", "reasoning_summary"):
            reasoning_parts.append(item.get("content") or item.get("text", ""))

    # Location C fallback
    if reasoning := data.get("reasoning"):
        reasoning_parts.append(str(reasoning))

    return "".join(text_parts).strip(), "".join(reasoning_parts).strip()
```

(`BuilderClient._extract_text_and_reasoning()` in `client.py` is slightly
simpler but covers the same locations.)

### 6.4 What Reasoning Is Used For

- `BuilderClient.chat_structured_with_reasoning()` returns `(parsed_model, reasoning)`.
  The reasoning is stored in `PlannedBuild.reasoning` and logged, but not
  included in downstream LLM prompts.
- `LLMClient.chat_with_reasoning()` / `generate_with_reasoning()` return the
  reasoning string to the caller for inspection or logging.
- Reasoning is never shown to the end user.

---

## 7. Tool Calling

### 7.1 Tool Definition Format

Tools are sent in OpenAI function format:

```json
{
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "commit_changes",
        "description": "Commit staged changes to the worktree.",
        "parameters": {
          "type": "object",
          "properties": {
            "message": {"type": "string"},
            "worktree_path": {"type": "string"}
          },
          "required": ["message", "worktree_path"],
          "additionalProperties": false
        }
      }
    }
  ],
  "tool_choice": "auto"
}
```

MCP-format tools (`{name, description, input_schema}`) are normalised to this
format by `LLMClient._normalize_tool_definition()` before being included.

### 7.2 Tool Call Response Shape

Azure may return tool calls in any of three locations in `data`:

```
data["output"][]                        type = "tool_call" | "function_call"
data["output"][message]["content"][]    type = "tool_call" | "function_call"
data["tool_calls"][]                    direct list (legacy / fallback)
```

A single tool call object looks like:

```json
{
  "type": "tool_call",
  "call_id": "call_abc123",
  "name": "commit_changes",
  "arguments": {"message": "feat: add feature", "worktree_path": "/app/worktrees/abc"}
}
```

Or in the older Chat Completions–style:

```json
{
  "type": "function_call",
  "id": "call_abc123",
  "function": {
    "name": "commit_changes",
    "arguments": "{\"message\": \"feat: add feature\"}"
  }
}
```

`_extract_tool_calls()` normalises both shapes into a flat list. Argument
extraction tries keys in priority order:
`arguments` → `input` → `params` → `function.arguments`.
String arguments are JSON-decoded; decode failures fall back to `{"raw": ...}`.

The `call_id` is extracted from: `call_id` → `id` → `tool_call_id`.

### 7.3 Tool Calling Loop

After the initial call returns tool calls, `LLMClient._post_response()` enters
a bounded loop:

```
initial call
    → if tool_calls empty: return data
    → while tool_calls and tool_round < max_tool_rounds:
          for each tool_call:
              result = execute_tool(tool_call)
              append {"role": "user",
                      "content": "[TOOL_RESULT tool=name id=call_id] {result_json}"}
              to input_messages
          next call: payload["input"] = input_messages
          tool_calls = extract_tool_calls(new data)
          tool_round += 1
    → if tool_calls still present: log warning, return data
```

**Tool result injection format:**

```
[TOOL_RESULT tool=<name> id=<call_id>] {"result": ..., "error": ...}
```

Results are injected as plain-text `"user"` role messages rather than `"tool"`
role messages. This is intentional: Azure's Responses API accepts `tool` role
in `input[]` but the implementation uses plain-text injection for compatibility
with models that do not strictly follow the tool-result protocol.

**Repeat call guard:** A signature `"<tool_name>:<sorted_json_args>"` is hashed
per call. If the same signature is seen twice, the second invocation returns
`{"error": "Repeated tool call detected: <name>"}` without executing.

**Bounded rounds:** `max_tool_rounds` defaults to `4`. When the loop exits with
pending tool calls a warning is emitted. The last `data` object (possibly with
unexecuted tool calls still listed) is returned.

### 7.4 Tool Calling in the LangChain (DeepAgent) Path

In `builder_progenitor`'s execution graph the tools are LangGraph `StructuredTool`
instances passed directly to `create_deep_agent()`. The LangGraph framework
manages the tool-calling loop internally using LangChain's `ChatOpenAI` bind
interface. The codebase does not implement a manual tool loop for this path.

---

## 8. Response Parsing Reference

### 8.1 Top-Level Response Object

```json
{
  "id":     "resp_abc123",
  "object": "response",
  "model":  "gpt-5.2-chat",
  "status": "completed",
  "output": [ ... ],
  "usage": {
    "input_tokens":  120,
    "output_tokens": 45,
    "total_tokens":  165
  },
  "incomplete_details": null,
  "content_filters": []
}
```

The validation script (`flash_test_azure_responses_contract.py`) asserts:
- `object == "response"`
- `id` is present
- `model` is present
- `output` is a list

### 8.2 `output[]` Item Types

| `type` | Meaning | Key fields |
|---|---|---|
| `"message"` | The primary assistant response | `role`, `content: [{type, text}]` |
| `"reasoning"` | Model's reasoning trace | `summary: [{type:"summary_text", text}]` |
| `"tool_call"` | Tool invocation request | `call_id`, `name`, `arguments` |
| `"function_call"` | Legacy tool invocation (Chat Completions compat) | `id`, `function.name`, `function.arguments` |
| `"output_text"` | Direct text shorthand | `text` |
| `"text"` | Alias for `output_text` | `text` |
| `"refusal"` | Model refusal | `refusal` or `text` |

### 8.3 Message Content Part Types

| `type` | Meaning |
|---|---|
| `"output_text"` | Plain assistant text |
| `"text"` | Alias for `output_text` |
| `"reasoning"` | Reasoning fragment inside a message |
| `"reasoning_text"` | Reasoning alias |
| `"reasoning_summary"` | Reasoning alias |
| `"summary_text"` | Reasoning summary fragment |
| `"refusal"` | Model refusal inside a message |
| `"tool_call"` | Tool call inside message content |

### 8.4 Empty Response Diagnostics

When `_extract_text_and_reasoning()` produces an empty string it logs a
structured diagnostic:

```
status:             data["status"]             e.g. "incomplete"
incomplete_details: data["incomplete_details"] e.g. {"reason": "max_output_tokens"}
truncation:         data["truncation"]
content_filters:    data["content_filters"]    list of filter result dicts
keys:               list(data.keys())[:12]
top_text:           data["text"]               (repr, first 200 chars)
output_summaries:   per-item type/role/status/keys/content_shape (first 5 items)
```

`BuilderClient.chat_structured_with_reasoning()` additionally raises a
`RuntimeError` with a human-readable message that includes `status`,
`incomplete_details.reason`, and any blocked content filter flags.

### 8.5 Inline Tool Markup Sanitization

`LLMClient._sanitize_user_visible_text()` strips inline tool-call traces that
some providers emit as plain text (e.g. `tool_name{...json...}`) before the text
is returned to callers:

```python
INLINE_TOOL_MARKUP_PATTERN = re.compile(r".*?", re.DOTALL)
cleaned = INLINE_TOOL_MARKUP_PATTERN.sub("", text)
```

---

## 9. Error Handling

| Error | When | Handling |
|---|---|---|
| `httpx.HTTPStatusError` (400 temperature) | `POST /responses` with unsupported temperature | Retry once without `temperature` field |
| `httpx.HTTPStatusError` (other 4xx/5xx) | Any `POST /responses` | Re-raised immediately after logging |
| Empty `output_text` with `status=incomplete` | Model hit `max_output_tokens` | `RuntimeError` with reason from `incomplete_details` |
| Empty `output_text` with content filter blocked | Azure content policy | `RuntimeError` with filter flag list |
| Empty `output_text` no provider error | Unexpected model behaviour | Log info with response shape; return empty string |
| `json.JSONDecodeError` on structured output text | Malformed model JSON | `json.loads` raises; propagates to caller |
| `pydantic.ValidationError` on structured model | Model output doesn't match schema | Propagates to caller |
| Tool loop `max_tool_rounds` exceeded | Too many sequential tool calls | Log warning; return last `data` with unprocessed tool calls |
| Repeated identical tool call | Model stuck in loop | Return `{"error": "Repeated tool call detected"}` as result, continue loop |

---

## 10. Quick Reference: Payload Anatomy

A fully populated request payload (all features enabled):

```json
{
  "model": "gpt-5.2-chat",
  "input": [
    {"role": "system",    "content": "System prompt text."},
    {"role": "user",      "content": "User request."},
    {"role": "assistant", "content": "Previous assistant turn."},
    {"role": "user",      "content": "[TOOL_RESULT tool=get_info id=call_1] {\"data\": 42}"}
  ],
  "max_output_tokens": 8192,
  "text": {
    "format": {
      "type":   "json_schema",
      "name":   "build_plan",
      "schema": {
        "type": "object",
        "additionalProperties": false,
        "required": ["task_name", "branch_name"],
        "properties": {
          "task_name":   {"type": "string"},
          "branch_name": {"type": "string"}
        }
      },
      "strict": true
    }
  },
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "get_info",
        "description": "Retrieve metadata.",
        "parameters": {
          "type": "object",
          "additionalProperties": false,
          "required": ["key"],
          "properties": {"key": {"type": "string"}}
        }
      }
    }
  ],
  "tool_choice": "none",
  "reasoning": {"summary": "auto"}
}
```

Note: `tool_choice: "none"` because `text.format` is set (§5.5). Without
structured output the field would be `"auto"`.
