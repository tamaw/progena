# Non-chat review follow-ups

Open findings from the PR-style review of `main` (`58339695956209f7bd6c35141c233eaeedf12ee2`) → `feat/chat-ui-ux-changes` (`b366e131657f1e693e6ee6d2f626572ef2ce626e`). The user selected the five chat rendering/history fixes for immediate implementation; the twelve findings below remain open. Source line numbers refer to that reviewed commit and may shift afterward.

The review used sixteen parallel reviewers. Runtime findings were checked with bounded, offline reproductions; no production mutations, external authentication calls, or deployments were performed. The documented fresh-database Progena rename cutover is intentional and is not a finding.

## Code execution and filesystem safety

### 1. P1 — Preserve a guest deadline during cancellation cleanup

- **Location:** [`control_nodes.py:373–377`](../src/progena/routines/control_nodes.py#L373-L377).
- **Problem:** Cancellation during a filesystem call cancels QuickJS evaluation, whose cancellation cleanup clears the guest deadline before running JavaScript cleanup handlers. An unbounded cleanup handler can then keep the worker alive while the node waits indefinitely for it.
- **Observed:** In a bounded child process with `quickjs-rs` 0.2.6, cancellation during a pending host read remained unfinished after 2.5 seconds despite the one-second guest execution limit. The child process was terminated without touching application data.
- **Required outcome:** Cancellation cleanup remains subject to an effective guest execution deadline, and cancellation/graph timeout reliably completes.
- **Status:** Open.

### 2. P2 — Drain actual threaded mutations before reporting cancellation complete

- **Location:** [`code_filesystem.py:92–98`](../src/progena/routines/code_filesystem.py#L92-L98).
- **Problem:** SDK filesystem mutations use `asyncio.to_thread()`. Cancelling the awaiting coroutine does not stop a running thread, but the bridge currently treats coroutine cleanup as operation completion.
- **Observed:** With a real SDK filesystem backend and a deliberately blocked write in a temporary directory, the bridge finished cancellation before the write completed; releasing the write created the file afterward.
- **Required outcome:** An invocation cannot report cancellation complete while its file mutation can still land or race with a later invocation.
- **Status:** Open.

### 3. P2 — Enforce no-overwrite with atomic destination creation

- **Locations:** [`code_filesystem.py:423–424`](../src/progena/routines/code_filesystem.py#L423-L424), copy/move destination handling at lines 501–502.
- **Problem:** The destination absence check and subsequent write are separate asynchronous operations. Concurrent callers can both pass the check and overwrite the same destination.
- **Observed:** Two concurrent creates against a real SDK filesystem backend both reported success for one temporary destination; only one payload remained.
- **Required outcome:** Only one competing destination creation succeeds, and an existing destination is never overwritten by write/copy/move operations advertising no-overwrite semantics.
- **Status:** Open.

## ChatGPT providers and model calls

### 4. P2 — Replace the explicit bearer header during token recovery

- **Location:** [`checkpoint_usage.py:194–197`](../src/progena/agent/checkpoint_usage.py#L194-L197); production headers originate in `agent/adapters.py`.
- **Problem:** `with_options(api_key=new_token)` preserves the explicit `Authorization` default header installed during production model construction. That stale header overrides the replacement API key.
- **Observed:** With OpenAI SDK 3.37.0 and an offline HTTP transport, the client held the replacement key but the actual request still sent the original synthetic bearer.
- **Required outcome:** Synchronous and asynchronous retry requests send the refreshed bearer, with no stale explicit authorization header.
- **Status:** Open.

### 5. P2 — Attach usage attribution once per streamed response

- **Locations:** [`checkpoint_usage.py:577–580`](../src/progena/agent/checkpoint_usage.py#L577-L580), synchronous equivalent at lines 482–485, and first-chunk attachment in `_stream()` / `_astream()`.
- **Problem:** Both the first stream chunk and finalized reasoning receive attribution. LangChain concatenates ordinary string metadata while aggregating chunks.
- **Observed:** An offline Responses stream aggregated through `model.astream()` produced `profile_name="reviewreview"`, `provider="chatgptchatgpt"`, a duplicated deployment, and concatenated timestamps.
- **Required outcome:** Completed streamed messages retain one correct profile/provider/deployment/timestamp attribution, including encrypted-reasoning completion.
- **Status:** Open.

### 6. P2 — Validate the provider/model pair actually committed by a profile PATCH

- **Location:** [`model_profiles.py:219–227`](../src/backend/settings/model_profiles.py#L219-L227).
- **Problem:** A provider-only PATCH validates the inherited deployment before acquiring the mutation lock, but its locked guard checks only whether the stored provider changed. A concurrent deployment-only edit can survive under the new provider without validation.
- **Observed:** A controlled interleaving acknowledged and persisted provider Q/model B even though Q's supplied catalogue contained only A.
- **Required outcome:** The committed provider/deployment pair is the validated pair; inherited-field changes cause safe revalidation or conflict rather than an invalid successful acknowledgement.
- **Status:** Open.

### 7. P2 — Retain a valid bearer while refresh is temporarily unavailable

- **Location:** [`providers.py:907–911`](../src/progena/settings/providers.py#L907-L911).
- **Problem:** Within the two-minute refresh leeway, a future `refresh_available_at` unconditionally rejects ordinary token lookup even if the bearer remains unexpired.
- **Observed:** A credential valid for another 90 seconds was rejected because refresh was unavailable for 30 seconds.
- **Required outcome:** Ordinary lookups may use the current unexpired bearer until refresh becomes permissible; an actually rejected or expired bearer must still fail safely.
- **Status:** Open.

## Workflow payload contracts

### 8. P2 — Keep browser correlation metadata outside manual payload validation

- **Location:** [`service.py:1036–1038`](../src/backend/workflows/service.py#L1036-L1038); the browser route appends `request_id` after initial validation in `workflows/routes.py`.
- **Problem:** The executor validates the augmented transport object against the manual payload schema. `additionalProperties: false` therefore rejects a valid user payload because of the injected `request_id`.
- **Observed:** Direct execution of the real browser-route coroutine and executor with an in-memory store returned HTTP 409, a misleading paused/disabled error, and created no run for an enabled workflow with valid strict input.
- **Required outcome:** Strict manual payload schemas work through the browser route while lifecycle events retain request correlation separately.
- **Status:** Fixed — browser route keeps `request_id` in the lifecycle event instead of the manual trigger payload; `test_strict_manual_trigger_payload_keeps_request_correlation_in_lifecycle_event`.

### 9. P2 — Apply manual defaults before tool-side validation

- **Location:** [`run_workflow.py:77–78`](../src/progena/tools/run_workflow.py#L77-L78).
- **Problem:** The tool validates the original payload before applying declared property defaults, unlike backend manual-trigger resolution.
- **Observed:** For a required integer property `n` defaulting to 3, the backend resolved `{}` to `{"n": 3}` while the tool rejected the same input as missing `n`.
- **Required outcome:** Backend and tool accept the same defaultable payloads and reject the same genuinely invalid payloads.
- **Status:** Fixed — shared schema default resolution now runs before `run_workflow` validation; `test_execute_applies_manual_defaults_before_validating_and_enqueueing`.

## Graph editor and Docker setup

### 10. P2 — Require run confirmation for filesystem-writing Code nodes

- **Location:** [`graphRunState.js:26–28`](../src/frontend/src/features/routines/editor/graphRunState.js#L26-L28).
- **Problem:** The side-effect classifier treats all Code nodes as pure, including nodes with `filesystem.access: "read_write"`. “Run to here” can execute mutations without the existing side-effect confirmation.
- **Observed:** The committed classifier returned `false` for an unexecuted filesystem-writing Code node; the inspector uses that classifier to decide whether to open confirmation.
- **Required outcome:** New execution of a write-capable Code node, including a predecessor of the selected target, requires confirmation. Pure/read-only Code nodes retain their intended behavior.
- **Status:** Open.

### 11. P3 — Consume Unicode identifier suffixes when accepting JavaScript completion

- **Location:** [`graphFieldHelpers.js:109–114`](../src/frontend/src/features/routines/editor/graphFieldHelpers.js#L109-L114).
- **Problem:** Acorn accepts Unicode JavaScript identifiers, but replacement-end detection consumes only ASCII `[\w$]` characters.
- **Observed:** Accepting `input.amount` with the caret after `am` in `input.amóunt` produced `input.amountóunt` rather than replacing the complete member identifier.
- **Required outcome:** Completion replaces the complete JavaScript identifier without preserving an unintended Unicode suffix.
- **Status:** Open.

### 12. P2 — Document creation of the external Postgres volume

- **Location:** [`DOCKER.md:55–60`](DOCKER.md#L55-L60); external declaration in [`docker-compose.progena.yml:236–238`](../docker/docker-compose.progena.yml#L236-L238).
- **Problem:** The README delegates installation to the Docker guide, whose prerequisites create only the external network and shared volume. Compose also requires externally created `progena-postgres-data`.
- **Evidence:** Checked the committed guide and Compose declarations. Docker startup was not run during review.
- **Required outcome:** A fresh installation following the documented procedure creates every required external resource before Compose startup, including `docker volume create progena-postgres-data` or an equivalent managed-volume configuration.
- **Status:** Open.
