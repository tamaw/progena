# A2A response contracts

An `a2a` routine node can opt into a strict reply contract and, separately, a
bounded same-content correction exchange. Nodes without `response_schema` keep
the legacy behavior: any successful reply text is accepted, and
`<output_key>_json` is filled by the lenient `extract_json_object` heuristic
when it can find an object.

Adding this capability to the repository opts no existing routine in. Turning
it on for a live routine is a separate, explicitly approved change.

## Configuration

```json
{
  "id": "review",
  "kind": "a2a",
  "output_key": "review",
  "config": {
    "agent_name": "draft-reviewer",
    "prompt_template": "Review draft {doc_id} and return your verdict.",
    "response_schema": {
      "type": "object",
      "additionalProperties": false,
      "required": ["verdict", "score"],
      "properties": {
        "verdict": { "enum": ["approve", "revise"] },
        "score": { "type": "integer", "minimum": 0, "maximum": 10 },
        "notes": { "type": "string" }
      },
      "if": { "properties": { "verdict": { "const": "revise" } } },
      "then": { "required": ["notes"] }
    },
    "response_correction": { "max_attempts": 1 },
    "retry": { "max_retries": 0 },
    "timeout": 120
  }
}
```

- `response_schema` is a JSON Schema object, or `{"$ref": "#/$defs/<name>"}`
  into the graph's own `$defs`. Nested references must also stay graph-local
  (`#...`). Remote, file, or undefined references are rejected when the graph is
  compiled.
- `response_correction.max_attempts` is the number of correction sends allowed
  after the original request. It defaults to 0 and is limited to 0–3, and it
  requires `response_schema`. The original request is never counted and never
  repeated.

Schema validity and validator availability (`jsonschema`, a direct dependency)
are checked when the graph is compiled. A configuration problem never reaches a
peer and never uses up a correction.

## Strict reply contract

The completed reply must be exactly one JSON object. Only JSON whitespace
(space, tab, CR, LF) may surround it. The contract rejects:

- code fences, prose before or after the object, and concatenated objects
- arrays and other non-object values
- duplicate keys at any depth
- `NaN`, `Infinity`, and numbers that overflow to infinity
- malformed JSON

The object is then validated offline against the schema. No reference is
fetched over the network or from the filesystem. Validation covers what the
schema can express: required fields, types, `enum`/`const`, numeric and length
bounds, forbidden extra properties, and declarative conditions
(`if`/`then`/`else`, `dependentRequired`, `anyOf`/`oneOf`/`not`). `format` is an
annotation only. Rules that live only in downstream nodes are outside the
contract. A downstream failure never triggers an upstream correction.

On acceptance the node writes the raw reply text to `<output_key>` and the
validated object to `<output_key>_json`, and appends the accepted reply to
`messages`. These are the same keys the legacy path uses.

## What qualifies for correction

Reply text is taken from peer-authored content only: a direct agent `Message`,
the task's status message, or its artifacts. It is never taken from task
history, which also contains the caller's request.

Only a **completed** reply that violates the contract qualifies for
correction. None of the following ever becomes a correction:

| Situation | Result |
| --- | --- |
| Transport error, timeout, or unsuccessful original send | Same `ValueError` as the legacy path. The node's retry policy applies. |
| Original task `failed`, `canceled`, `rejected`, `input-required`, `auth-required`, `working`, `submitted`, or unknown | `A2AReplyNotCompletedError` (a `ValueError`). The node's retry policy applies. |
| `asyncio` cancellation | Propagates unchanged. |

## Correction exchange

1. The original request is sent once.
2. If the reply is invalid and corrections remain, the node sends a correction
   to the same registry peer. The correction carries the `contextId` the peer
   returned, a fresh message ID, and **no** task ID. A completed task is never
   treated as resumable.
3. The correction text is fixed, trusted wording plus sanitized diagnostics and
   the top-level required/allowed property names. It tells the peer to restate
   the SAME feedback, keep its factual decisions, and not research, call tools,
   place or modify orders, send messages, or repeat any work. If the contract
   cannot be met without new facts, the peer is told to reply
   `CONTRACT_CORRECTION_UNAVAILABLE` rather than invent values.
4. The correction never includes the original action prompt, the previous
   reply, routine state, credentials, or other runs' data. The previous reply
   is reachable only through the peer's own protocol context.
5. Every corrected reply goes through the same strict validation.

Diagnostics contain codes (`missing_required`, `wrong_type`, `not_in_enum`,
`out_of_range`, `unexpected_property`, `trailing_content`, and so on), bounded
paths, and schema-side constraints. Offending values, reply-side key names
(shown as `<undeclared>`), validator messages, schema descriptions, and
non-identifier schema text are never included. At most 20 problems are
reported.

One deadline covers the original send and every correction. The node
`timeout`, or the execution-policy attempt timeout, starts when the node starts
and is not reset for each correction.

## Terminal failures and retries

Each of these raises `A2AContractTerminalError` with a fixed `code`:

| Code | Meaning |
| --- | --- |
| `contract_violation` | Corrections are disabled and the reply violated the contract. |
| `corrections_exhausted` | 1 + `max_attempts` replies were all invalid. |
| `no_protocol_context` | The peer returned no `contextId` to continue. |
| `context_mismatch` | A correction reply came back with a different or missing `contextId`. |
| `correction_send_failed` | The peer rejected the continuation, or a transport error occurred during a correction. |
| `correction_not_completed` | A correction reply was not a completed task or message. |
| `correction_declined` | The peer answered `CONTRACT_CORRECTION_UNAVAILABLE`. |
| `deadline_exceeded` | No time was left for another correction. |

The compiled retry predicates for `transient`, `model_response`, and `all`
exclude this error type. A terminal contract failure therefore never re-sends
the original request, whatever the retry configuration. An `error` edge still
routes it as usual. The node falls back to a fresh original request in none of
these cases.

Existing transport retries are unchanged. **If an author enables retries
(`max_retries > 0`), a transport failure of the original send can repeat the
original request.** Any deployment whose peer has side effects must use
`max_retries: 0`, and it needs separate idempotency and safety controls. This
feature does not change global retry defaults.

## Audit record

Each execution stores an ordered `a2a_contract` record in the node's
`run_meta` entry, in the run's own checkpoint:

- node and peer names, the correction budget, and the continuity limitation
- one entry per attempt: kind (`original`/`correction`), task/context/reply
  message IDs when available, task state, the raw reply, validity, and
  diagnostics
- the final `outcome`

The record is written on success through the node update. On failure it is
attached to the exception and persisted by the routine executor's existing
failed-node path (`subagent.py`, `compiled.aupdate_state`). Raw replies appear
only there. Error strings, `data.errors`, and the run-state UI event
(`read_run_state`) carry codes and summaries: in browser payloads each attempt's
`reply` is replaced by `reply_chars`. Only the accepted reply becomes normal
output.

Cancellation, or a LangGraph node-timeout watchdog that fires before the
node's own deadline, interrupts the node before it can return or raise its
record. Such runs have the usual timing entry but no attempt transcript.

## Limitations

- An A2A `contextId` provides routing continuity only. The client cannot verify
  that a peer remembers its previous reply, that it disables tools during a
  correction, or that it has no side effects. Prompts are not a sandbox.
- Enable correction only for peers separately verified to handle a correction
  as an output-only, read-only operation. Unverified use is a rollout
  limitation, not permission to replay work.
- Passing schema validation does not prove the corrected reply carries the
  same facts. A correction that needs new facts should stay invalid or be
  declined, and either outcome ends the exchange.
- Strict parsing rejects output the lenient path used to tolerate. This
  happens only for nodes that opt in.
