---
name: runtime-inspection
description: Inspect Progena's heartbeat status, runtime logs, session records, and read-only virtual documents to diagnose failures, stalls, and behavioral regressions.
allowed-tools: ls read_file grep glob
---

# Runtime Inspection

## When to Use
- The user asks why Progena failed, stalled, or repeated work
- The user asks for current runtime status or evidence from runtime activity
- A regression needs correlation with a conversation, registered agent, or repository scan

## Available Evidence

There is no `/data/` mount. Inspect the supported virtual documents instead:

- `/config/health.json` is the heartbeat-owned, read-only runtime status. It exposes the current lifecycle and recent health information.
- `/config/repo_scan.json` is the heartbeat-owned, read-only latest scan of declared repositories.
- `/config/agents.json` shows configured A2A peers and runtime tools; use it to distinguish a missing or disabled registration from a runtime failure.
- `/logs/` is the read-only configured log directory. List it before reading or searching a specific log.
- `/conversations/<date>_<title-slug>_<id8>.md` contains a chat record as YAML front matter plus IRC-style Markdown. It can provide the user-visible request and response sequence; session bodies are not runtime event logs.

These paths are virtual documents or read-only mounts, not local files to modify. Do not write while inspecting.

## Fast Triage

1. Read `/config/health.json`. If it is empty, the heartbeat may not have written its first status yet; do not report that as a failure by itself.
2. List `/logs/`, then use `grep` or `read_file` on the relevant recent log. Capture timestamps, error text, request/thread IDs, and agent names.
3. If the issue involves a declared repository or remote agent, read `/config/repo_scan.json` or `/config/agents.json` respectively.
4. When user-visible context matters, locate the relevant `/conversations/...md` record and correlate it with the status and log evidence.
5. Separate observed facts from the likely cause and name the smallest next inspection that would resolve remaining uncertainty.

## Response Format
- **Symptom**
- **Evidence** — cite virtual paths and relevant timestamps or identifiers
- **Likely cause** — mark it as a hypothesis when evidence is incomplete
- **Next check** — a specific supported path or search, not a shell command
