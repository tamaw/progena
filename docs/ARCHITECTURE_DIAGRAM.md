# Progenitor Dual-Loop Architecture Diagram

## System Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                    PROGENITOR AGENT                             │
│              Dual-Loop Architecture (loop.spec.md)              │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────── OUTER LOOP ───────────────────────────┐
│                         (Perpetual)                              │
│                                                                  │
│  ┌────────┐    ┌──────────┐    ┌────────────┐    ┌─────────┐  │
│  │ INTAKE │───▶│TIME CHECK│───▶│ENQUEUE DUE │───▶│ DECIDE  │  │
│  └────────┘    └──────────┘    └────────────┘    └────┬────┘  │
│                                                         │        │
│                                                         ▼        │
│                        ┌────────────────────────────────┴─────┐ │
│                        │  Simple Task?                        │ │
│                        │  • Direct answer                     │ │
│                        │  • Quick delegation                  │ │
│                        └──────┬────────────────────────┬──────┘ │
│                               │ YES                NO   │        │
│                               │                    │            │
│  ┌──────────────────────────┐│               ┌────▼───────┐   │
│  │                           ││               │            │   │
│  │      INNER LOOP          ││               │   INNER    │   │
│  │   (skip for simple)      ││               │   LOOP     │   │
│  │                           ││               │            │   │
│  └──────────────────────────┘│               └────┬───────┘   │
│                               │                    │            │
│                               ▼                    │            │
│                        ┌──────────┐                │            │
│                        │ RESPOND  │◀───────────────┘            │
│                        └─────┬────┘                             │
│                              │                                  │
│                              ▼                                  │
│                        ┌────────────┐                           │
│                        │CYCLE LIMIT │                           │
│                        └──────┬─────┘                           │
│                               │                                 │
│                ┌──────────────┴──────────────┐                 │
│                │                              │                 │
│                ▼                              ▼                 │
│         ┌──────────┐                   ┌──────────┐            │
│         │   IDLE   │                   │   END    │            │
│         └────┬─────┘                   └──────────┘            │
│              │                                                  │
│              └──────────────────────────┐                      │
│                                         │                      │
└─────────────────────────────────────────┼──────────────────────┘
                                          │
                                          ▼ (repeat)
```

## Inner Loop Detail

```
┌──────────────────── INNER LOOP (Iteration) ────────────────────┐
│                                                                 │
│  Repeats until: Verification PASSES or Stop Conditions MET     │
│                                                                 │
│  ┌──────────┐                                                  │
│  │ OBSERVE  │  ◀── Start here on first iteration               │
│  └────┬─────┘      or when continuing from LEARN               │
│       │                                                         │
│       │  • Build initial ideal state (iteration 0)             │
│       │  • Refine ideal state (subsequent iterations)          │
│       │  • Add anti-criteria for discovered issues             │
│       │  • Tighten criteria that were too loose                │
│       │                                                         │
│       ▼                                                         │
│  ┌──────────┐                                                  │
│  │  THINK   │                                                  │
│  └────┬─────┘                                                  │
│       │                                                         │
│       │  • Generate approaches to reach ideal state            │
│       │  • Analyze trade-offs                                  │
│       │  • Identify risks                                      │
│       │  • Consider constraints                                │
│       │                                                         │
│       ▼                                                         │
│  ┌──────────┐                                                  │
│  │   PLAN   │                                                  │
│  └────┬─────┘                                                  │
│       │                                                         │
│       │  • Create work items                                   │
│       │  • Break down complex tasks                            │
│       │  • Assign to tools/agents                              │
│       │                                                         │
│       ▼                                                         │
│  ┌──────────┐                                                  │
│  │  BUILD   │  (ACT node - produces candidate code/agent)       │
│  └────┬─────┘                                                  │
│       │                                                         │
│       │  • Generate code / agent files                          │
│       │  • Run Copilot CLI or other code-gen tools              │
│       │  • Produce candidate artifacts                          │
│       │                                                         │
│       ▼                                                         │
│  ┌──────────┐                                                  │
│  │ EXECUTE  │  ◀── Start agent & run test prompts               │
│  └────┬─────┘                                                  │
│       │                                                         │
│       │  • Start the built agent (HTTP or stdio)                │
│       │  • Run chat prompts / test cases against it             │
│       │  • Collect responses & execution data                   │
│       │  • Store results in last_iteration_output               │
│       │                                                         │
│       ▼                                                         │
│  ┌──────────┐                                                  │
│  │ VALIDATE │  (Uses execution data to check correctness)       │
│  └────┬─────┘                                                  │
│       │                                                         │
│       │  • Run syntax/lint checks on generated code             │
│       │  • Compare execution output to expected results         │
│       │  • Check for errors/exceptions during EXECUTE           │
│       │                                                         │
│       ▼                                                         │
│  ┌──────────┐                                                  │
│  │  VERIFY  │  ◀── CRITICAL: Scores against ideal state        │
│  └────┬─────┘                                                  │
│       │                                                         │
│       │  • Score each success criterion (0-1)                  │
│       │  • Check anti-criterion violations                     │
│       │  • Calculate weighted overall score                    │
│       │  • Identify gaps                                       │
│       │  • Suggest next actions                                │
│       │  • Generate verification report                        │
│       │                                                         │
│       ▼                                                         │
│  ┌──────────┐                                                  │
│  │  LEARN   │                                                  │
│  └────┬─────┘                                                  │
│       │                                                         │
│       │  • Analyze what worked                                 │
│       │  • Analyze what failed                                 │
│       │  • Capture lessons                                     │
│       │  • Generate insights                                   │
│       │  • Update strategy                                     │
│       │                                                         │
│       ▼                                                         │
│  ┌──────────────────────────────┐                             │
│  │   CHECK STOP CONDITIONS      │                             │
│  │                              │                             │
│  │  • Min score reached?        │                             │
│  │  • Max iterations hit?       │                             │
│  │  • Time budget exhausted?    │                             │
│  │  • Tool budget exhausted?    │                             │
│  └────────┬─────────────────────┘                             │
│           │                                                     │
│    ┌──────┴───────┐                                           │
│    │              │                                           │
│    ▼ NO           ▼ YES                                       │
│  OBSERVE       RESPOND                                        │
│  (iterate)     (done)                                         │
│                                                                │
└────────────────────────────────────────────────────────────────┘
```

Notes:
- BUILD produces code/agent artifacts (via Copilot CLI or other tools).
- EXECUTE starts the built agent and runs test prompts to gather real execution data.
- VALIDATE uses execution results to check correctness (syntax, expected outputs, errors).
- VERIFY scores execution + validation data against ideal state criteria.
- REGISTER is NOT part of the inner loop; users invoke it separately via chat tool when ready to deploy.

## Ideal State Structure

```
┌──────────────────── IDEAL STATE ─────────────────────┐
│                                                       │
│  Task: "Build a Python agent that analyzes code"     │
│                                                       │
│  ┌─────────────────────────────────────────────────┐ │
│  │ SUCCESS CRITERIA (ranked, measurable)           │ │
│  ├─────────────────────────────────────────────────┤ │
│  │ 1. Agent parses Python files                    │ │
│  │    Target: 0.9 | Weight: 2.0                    │ │
│  │    Measure: "Run on test files"                 │ │
│  │                                                  │ │
│  │ 2. Agent identifies patterns                    │ │
│  │    Target: 0.8 | Weight: 1.5                    │ │
│  │    Measure: "Check pattern detection"           │ │
│  │                                                  │ │
│  │ 3. Output is maintainable                       │ │
│  │    Target: 0.7 | Weight: 1.0                    │ │
│  │    Measure: "Code quality heuristics"           │ │
│  └─────────────────────────────────────────────────┘ │
│                                                       │
│  ┌─────────────────────────────────────────────────┐ │
│  │ ANTI-CRITERIA (actively penalized)              │ │
│  ├─────────────────────────────────────────────────┤ │
│  │ • Avoid verbose output (severity: 0.5)          │ │
│  │ • No security vulnerabilities (severity: 2.0)   │ │
│  │ • Avoid unnecessary dependencies (severity: 1.0)│ │
│  └─────────────────────────────────────────────────┘ │
│                                                       │
│  ┌─────────────────────────────────────────────────┐ │
│  │ QUALITY BAR                                      │ │
│  ├─────────────────────────────────────────────────┤ │
│  │ "Euphoric surprise" = Agent that not only works │ │
│  │ but is elegant, well-documented, and includes   │ │
│  │ examples that make users say 'wow!'             │ │
│  └─────────────────────────────────────────────────┘ │
│                                                       │
│  ┌─────────────────────────────────────────────────┐ │
│  │ STOP CONDITIONS                                  │ │
│  ├─────────────────────────────────────────────────┤ │
│  │ • Min score: 0.8                                 │ │
│  │ • Max iterations: 5                              │ │
│  │ • Time limit: 300s                               │ │
│  │ • Tool budget: 20 calls                          │ │
│  └─────────────────────────────────────────────────┘ │
│                                                       │
└───────────────────────────────────────────────────────┘
```

## Verification Report

```
┌────────────── VERIFICATION REPORT ──────────────┐
│                                                  │
│  Iteration: 2                                    │
│  Timestamp: 2025-01-20T12:34:56Z                 │
│                                                  │
│  ┌────────────────────────────────────────────┐ │
│  │ CRITERION SCORES                           │ │
│  ├────────────────────────────────────────────┤ │
│  │ ✓ Agent parses Python files                │ │
│  │   Score: 0.9 / 0.9  [PASS]                 │ │
│  │   Evidence: "All 10 test files parsed"     │ │
│  │                                             │ │
│  │ ✓ Agent identifies patterns                │ │
│  │   Score: 0.85 / 0.8  [PASS]                │ │
│  │   Evidence: "Detected 8/10 patterns"       │ │
│  │                                             │ │
│  │ ✗ Output is maintainable                   │ │
│  │   Score: 0.65 / 0.7  [FAIL]                │ │
│  │   Evidence: "Some complexity issues"       │ │
│  └────────────────────────────────────────────┘ │
│                                                  │
│  Overall Score: 0.82 (weighted)                  │
│  Status: PASSED (threshold: 0.8)                 │
│                                                  │
│  ┌────────────────────────────────────────────┐ │
│  │ GAPS                                       │ │
│  ├────────────────────────────────────────────┤ │
│  │ • Maintainability below target             │ │
│  └────────────────────────────────────────────┘ │
│                                                  │
│  ┌────────────────────────────────────────────┐ │
│  │ NEXT ACTIONS                               │ │
│  ├────────────────────────────────────────────┤ │
│  │ 1. Refactor complex functions              │ │
│  │ 2. Add documentation strings               │ │
│  └────────────────────────────────────────────┘ │
│                                                  │
└──────────────────────────────────────────────────┘
```

## State Flow

```
USER INPUT
    │
    ▼
┌─────────────────┐
│  Task State     │
│                 │
│  • Messages     │
│  • Backlog      │
│  • Schedule     │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Decide         │
└────────┬────────┘
         │
    ┌────┴────┐
    │         │
    ▼         ▼
Simple     Complex
Task       Task
    │         │
    │         ▼
    │    ┌─────────────────┐
    │    │ Ideal State     │
    │    │ • Criteria      │
    │    │ • Anti-criteria │
    │    │ • Quality bar   │
    │    │ • Stop conds    │
    │    └────────┬────────┘
    │             │
    │             ▼
    │    ┌─────────────────┐
    │    │ Iteration Loop  │
    │    │ (OBSERVE→LEARN) │
    │    └────────┬────────┘
    │             │
    │             ▼
    │    ┌─────────────────┐
    │    │ Verification    │
    │    │ Report          │
    │    └────────┬────────┘
    │             │
    └─────────────┘
                  │
                  ▼
            ┌──────────┐
            │ RESPONSE │
            └──────────┘
```

## Component Dependencies

```
┌──────────────────────────────────────────────────────┐
│                   State Manager                       │
│  • Persists state                                    │
│  • Tracks iterations                                 │
│  • Logs decisions                                    │
└───────┬──────────────────────────────────────────────┘
        │
        ├─────────────────┬──────────────────┐
        │                 │                  │
        ▼                 ▼                  ▼
┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│ IdealState   │  │  Verifier    │  │ Phase Nodes  │
│  Builder     │  │              │  │              │
│              │  │ • Scores     │  │ • OBSERVE    │
│ • Extracts   │  │ • Reports    │  │ • THINK      │
│ • Refines    │  │ • Checks     │  │ • PLAN       │
│              │  │              │  │ • BUILD      │
│              │  │              │  │ • EXECUTE    │
│              │  │              │  │ • VERIFY     │
│              │  │              │  │ • LEARN      │
└──────────────┘  └──────────────┘  └──────────────┘
        │                 │                  │
        └─────────────────┴──────────────────┘
                          │
                          ▼
                  ┌──────────────┐
                  │  LLM Client  │
                  │              │
                  │ • Generates  │
                  │ • Judges     │
                  │ • Refines    │
                  └──────────────┘
```

## Iteration Timeline

```
TIME ─────────────────────────────────────────────────▶

ITER 0:  OBSERVE → THINK → PLAN → BUILD → EXECUTE → VALIDATE → VERIFY → LEARN
         └─ Build    Generate  Create   Code    Run      Check    Score:   Lessons:
            ideal    approaches items   gen     agent    results  0.65     "Need tests"
            state                                                  ✗

ITER 1:  OBSERVE → THINK → PLAN → BUILD → EXECUTE → VALIDATE → VERIFY → LEARN
         └─ Refine   New       Add     Add      Run      Check    Score:   Lessons:
            criteria approach  tests   tests    agent    results  0.75     "Tests help"
                                                                   ✗

ITER 2:  OBSERVE → THINK → PLAN → BUILD → EXECUTE → VALIDATE → VERIFY → LEARN
         └─ Tighten  Optimize  Fix     Refine   Run      Check    Score:   Lessons:
            targets  approach  gaps    code     agent    results  0.85     "Quality up!"
                                                                   ✓

         STOP: Score threshold reached (0.85 >= 0.8)

         RESPOND: Return output + verification summary
```

---

**Key Insight**: The dual-loop architecture ensures quality by iteratively
improving output until measurable criteria are met, with the ideal state
serving as the primary control surface throughout the process.
