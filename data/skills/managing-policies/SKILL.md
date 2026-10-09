---
name: managing-policies
description: Create, inspect and edit execution policies under /policies/ and select them for routines. Use when changing routine retries, timeouts, default model profiles, concurrency, fan-out defaults or caching.
allowed-tools: read_file write_file edit_file ls structured_output generate_id
---

# Managing policies

Policies are Postgres JSON documents exposed as `/policies/<id>.json`.
Read `/schemas/policy.schema.v1.json` before creating or reshaping a policy.
Use structured_output with that schema, then write the validated document.
For a focused edit, read the existing document and use edit_file. Every write
is validated and captures an immutable policy revision. Report success only
after persistence succeeds. The id must match the filename.

A routine selects one policy through `policy_id` in `/routines/<id>.json`.
The default selection is `default`, an ordinary persisted, editable policy.
Creating a policy does not change a routine's selection: edit the routine
explicitly when requested. For a new policy obtain a fresh id with generate_id.
Do not overwrite the selected policy when the user requests a new policy.

Values are execution defaults. Node-specific retries, timeout, model_profile,
max_items and eligible-node cache settings override the respective defaults.
Graph concurrency and step limits constrain execution. The fan-out limit is a
default for the node's max_items and uses its existing truncation behaviour.
Retry categories are transient, model_response (also ValueError), and all.
Only Prompt, Classify and Eval nodes may cache results. Native LangGraph
caching uses matching node inputs and a shared cache backend; it may reuse
results across runs. TTL is seconds; null never expires, zero expires immediately.

Editing a shared policy affects new runs of all routines selecting it. Existing
paused runs retain their recorded policy values. Read relevant routines before
changing a shared policy; create a copy when only one routine should change.
The run records policy id, revision and effective values. Arbitrary run-level
policy overrides, budgets and approval expiry are not supported.
