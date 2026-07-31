---
name: runtime-inspection
description: Inspect runtime state and logs through /data and /logs mounts to diagnose failures, stalls, and behavioral regressions
allowed-tools: read_file shell
---

# Runtime Inspection Skill

## When to Use
- User asks why Progenitor failed, stalled, or looped
- User asks for current runtime status
- User asks for evidence from state/log files

## Mount Usage
- `/data/` is read-only runtime data (state, registry, schedules, checkpoints).
- `/logs/` is read-only runtime logs.
- Never write to these paths in this skill.

## Fast Triage Sequence
1. Check recent timestamps and liveness markers.
2. Scan latest errors/warnings in logs (health/status/current-state are Postgres
   documents now, not files under `/data/` - logs are the file-based evidence).
3. Correlate state transitions with log events.
4. Report probable root cause and concrete next check.

## Response Format
- `Symptom`
- `Evidence`
- `Likely cause`
- `Next command/check`
