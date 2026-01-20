# Progenitor Agent – Minimal Build Spec (Last Algorithm)

## Purpose

General‑purpose agent that moves from **Current State → Ideal State** using an outer “ideal‑state” loop and an inner improvement loop until verification meets quality.

---

## Core Model

### Outer Loop: Ideal State

Define once, refine continuously.

* Success criteria (ranked, measurable)
* Anti‑criteria (what to avoid)
* Quality bar (“euphoric surprise” for this user)
* Verification methods
* Stop conditions

Ideal state is the primary control surface. Treat it as first‑class state.

---

### Inner Loop (repeat until done)

1. OBSERVE – refine ideal state, add anti‑criteria, tighten criteria
2. THINK – generate approaches, trade‑offs, risks
3. PLAN – choose experiment and expected measurable outcome
4. BUILD – produce candidate output
5. EXECUTE – run tools if needed
6. VERIFY – score vs ideal state
7. LEARN – update strategy + ideal state

Exit when verification meets stop conditions or limits reached.

---

## Required Components

* Orchestrator (outer + inner loop controller)
* IdealStateBuilder (extract criteria, anti‑criteria, quality bar)
* Phase Handlers (observe, think, plan, build, execute, verify, learn)
* Verifier (scores outputs against criteria)
* Tool Router (MCP / A2A / tools)
* Memory Store (task state, iterations, lessons)

---

## Task State (persisted)

* Task ID, run ID
* Ideal state (versioned)
* Iteration count
* Current phase
* Artefacts
* Verification reports
* Lessons learned

---

## Verification Contract

Every iteration produces:

* Score per criterion
* Pass/fail summary
* Evidence or heuristic notes
* Gaps and next actions

Final output must include a compact verification summary.

---

## Stop Conditions

* Minimum verification score reached
* Max iterations hit
* Tool/time budget exhausted

If stopped early, return best artefact plus unmet criteria.

---

## Behavioural Rules

* Always build or refine ideal state before serious work
* Verification drives control flow
* Prefer iteration over single‑shot answers
* Anti‑criteria actively penalise fluff, violations, and user annoyances

---

## Acceptance Signals

* Verification score improves across iterations
* Constraints are enforced automatically
* Anti‑criteria prevent common failure modes
* Final output matches defined ideal state, not just the prompt
