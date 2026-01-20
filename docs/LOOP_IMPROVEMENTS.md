# Progenitor Loop Improvements - Implementation of loop.spec.md

This document describes the improvements made to implement the dual-loop architecture specified in `loop.spec.md`.

## Overview

The Progenitor agent now implements a sophisticated dual-loop architecture:

- **OUTER LOOP**: Ideal State management - defines and refines the target state
- **INNER LOOP**: Iterative improvement - OBSERVE → THINK → PLAN → BUILD → EXECUTE → VERIFY → LEARN

## Architecture

### Dual-Loop System

```
OUTER LOOP (Perpetual):
  Intake → TimeCheck → EnqueueDue → Decide
    ↓
  [INNER LOOP if complex task]
    ↓
  Respond → CycleLimit → Idle → [repeat]

INNER LOOP (Until verification passes):
  OBSERVE → THINK → PLAN → BUILD → VERIFY → LEARN
    ↓
  [iterate or respond]
```

## New Components

### 1. Ideal State Management (`state/ideal_state.py`)

**IdealState Model**: The primary control surface for the agent loop.

- **Success Criteria**: Ranked, measurable criteria with target scores
- **Anti-Criteria**: Things to actively avoid/penalize
- **Quality Bar**: "Euphoric surprise" standard for the user
- **Verification Methods**: How to verify each criterion
- **Stop Conditions**: When to stop iterating (min_score, max_iterations, time_limit, tool_budget)

**IdealStateBuilder**: Builds and refines ideal state from task descriptions.

- Extracts criteria from user input using LLM
- Refines criteria based on iteration results (OBSERVE phase)
- Adds anti-criteria for discovered failure modes

### 2. Verification System (`state/verifier.py`)

**Verifier**: Scores outputs against ideal state criteria.

Every iteration produces a **VerificationReport** with:
- Score per criterion (0-1 scale)
- Pass/fail summary
- Evidence or heuristic notes
- Gaps (unmet criteria)
- Next actions

Verification methods:
- **Test-based**: Run actual tests (pytest, etc.)
- **Heuristic**: Rules-based scoring
- **LLM-judge**: Use LLM to evaluate quality

### 3. New Phase Nodes

#### OBSERVE (`nodes/observe.py`)
- Builds initial ideal state (first iteration)
- Refines ideal state based on verification results
- Adds anti-criteria for discovered issues
- Tightens criteria that were too loose

#### THINK (`nodes/think.py`)
- Generates approaches to reach ideal state
- Analyzes trade-offs and risks
- Considers constraints and previous failures

#### VERIFY (`nodes/verify_loop.py`)
- Scores output against all criteria
- Checks for anti-criterion violations
- Produces detailed verification report
- Calculates weighted overall score

#### LEARN (`nodes/learn.py`)
- Analyzes what worked and what failed
- Captures lessons learned
- Generates insights for next iteration
- Updates strategy

### 4. Updated State Model

Added to `ProgenitorState`:

```python
# Ideal State Loop (loop.spec.md)
ideal_state: Optional[Any]  # IdealState object
current_iteration: int  # Inner loop iteration count
iteration_start_time: float  # When iteration started

# Inner Loop Phases
current_approaches: list[dict]  # From THINK phase
thinking_output: Optional[str]  # THINK output
last_iteration_output: Optional[Any]  # BUILD output

# Verification
last_verification_report: Optional[Any]  # VerificationReport
verification_passed: bool
verification_score: float

# Learning
lessons_learned: list[dict]  # Accumulated lessons
current_lesson: Optional[dict]  # Current iteration lesson

# Stop condition tracking
should_iterate: bool  # Continue iterating?
iteration_stop_reason: Optional[str]  # Why stopped
```

### 5. Dual-Loop Graph (`graph_dual_loop.py`)

New LangGraph topology implementing loop.spec.md:

**Routing Logic**:
- `route_after_decide`: Simple tasks bypass inner loop, complex tasks enter OBSERVE
- `should_iterate`: Checks stop conditions after LEARN phase
- `route_after_act_build`: Routes through validation if needed
- `should_continue_outer`: Outer loop control (unchanged)

**Inner Loop Flow**:
```
OBSERVE (refine ideal state)
  ↓
THINK (generate approaches)
  ↓
PLAN (create work items)
  ↓
BUILD (execute/produce output)
  ↓
VERIFY (score against criteria)
  ↓
LEARN (capture lessons)
  ↓
[Check stop conditions]
  → Continue iterating (back to OBSERVE)
  → Stop and respond (verification passed or limits hit)
```

## Usage

### Configuration

The dual-loop system is enabled by default. To use the original graph:

```python
# In config or initialization
config.runtime.use_dual_loop = False
```

### Example Flow

1. **User Input**: "Build a Python agent that can analyze code"

2. **DECIDE**: Determines this requires planning → enters inner loop

3. **OBSERVE (Iteration 0)**: Builds ideal state:
   - Success Criteria: "Agent can parse Python files", "Agent identifies patterns", etc.
   - Anti-Criteria: "Avoid overly complex output", "No security vulnerabilities"
   - Stop Conditions: min_score=0.8, max_iterations=5

4. **THINK**: Generates approaches:
   - "Use AST parsing for code analysis"
   - "Implement pattern detection with regex"
   - Trade-offs: Speed vs accuracy

5. **PLAN → BUILD**: Creates work items and builds the agent

6. **VERIFY**: Scores output:
   - Criterion 1 (parsing): 0.9 ✓
   - Criterion 2 (patterns): 0.6 ✗
   - Overall: 0.75 (below threshold)

7. **LEARN**: Captures lessons:
   - "Parsing works well with AST"
   - "Pattern detection needs improvement"
   - Adjustment: "Add more pattern examples"

8. **OBSERVE (Iteration 1)**: Refines ideal state:
   - Adds anti-criterion: "Avoid false positives in pattern matching"
   - Tightens pattern detection criterion

9. **Repeat** until verification passes or limits reached

10. **RESPOND**: Returns best output with verification summary

## Key Features

### 1. Verification-Driven Control Flow
- Verification score determines when to stop iterating
- Each criterion has measurable target
- Anti-criteria actively penalize violations

### 2. Continuous Refinement
- Ideal state evolves based on results
- Criteria tighten as capabilities improve
- Anti-criteria prevent recurring failures

### 3. Learning Accumulation
- Lessons captured from each iteration
- Insights guide strategy adjustments
- Patterns remembered for future tasks

### 4. Stop Conditions
Iteration stops when:
- **Min Score Reached**: Verification passes threshold
- **Max Iterations**: Safety limit (default: 5)
- **Time Limit**: Budget exhausted
- **Tool Budget**: Too many tool calls

### 5. Graceful Degradation
If stopped early:
- Returns best artifact achieved so far
- Includes unmet criteria in response
- Provides verification summary

## State Tracking

### Iteration Logging

State manager tracks:
- `log_iteration_start(iteration, ideal_state)`
- `log_iteration_end(iteration, verification_report, stop_reason)`
- `save_verification_report(iteration, report)`

Logs include:
- Ideal state version and criteria count
- Verification scores and pass/fail status
- Gaps and stop reasons

### Decision Log Entries

New events in `decisions.jsonl`:
- `ideal_state_created`: Initial ideal state built
- `ideal_state_refined`: Criteria updated
- `thinking_phase`: Approaches generated
- `verification`: Verification scores
- `learning_phase`: Lessons captured
- `iteration_start` / `iteration_end`: Iteration boundaries

## Benefits

1. **Quality Control**: Verification ensures output meets standards
2. **Iterative Improvement**: Multiple attempts to reach ideal state
3. **Failure Prevention**: Anti-criteria catch common issues
4. **Transparency**: Verification reports show exactly what was met/missed
5. **Efficiency**: Stops as soon as quality threshold reached
6. **Resilience**: Graceful degradation if limits hit

## Files Changed/Added

### New Files:
- `app/progenitor/state/ideal_state.py` - Ideal state models and builder
- `app/progenitor/state/verifier.py` - Verification system
- `app/progenitor/nodes/observe.py` - OBSERVE phase
- `app/progenitor/nodes/think.py` - THINK phase
- `app/progenitor/nodes/verify_loop.py` - VERIFY phase
- `app/progenitor/nodes/learn.py` - LEARN phase
- `app/progenitor/graph_dual_loop.py` - Dual-loop graph topology

### Modified Files:
- `app/progenitor/state/models.py` - Added ideal state fields to ProgenitorState
- `app/progenitor/nodes/__init__.py` - Export new nodes
- `app/progenitor/main.py` - Support both graphs
- `app/progenitor/state/manager.py` - Iteration tracking methods

## Future Enhancements

1. **Structured LLM Output**: Use schema-constrained generation for ideal state extraction
2. **Custom Verifiers**: Plugin system for domain-specific verification
3. **Verification Caching**: Reuse verification results for similar outputs
4. **Multi-Objective Optimization**: Balance competing criteria
5. **Human-in-Loop**: Request human verification for critical criteria
6. **Adaptive Stop Conditions**: Learn optimal thresholds from history

## Testing

Run tests to verify the implementation:

```bash
# Test individual components
pytest tests/test_ideal_state.py
pytest tests/test_verifier.py

# Test full loop
pytest tests/test_dual_loop_integration.py

# Test original graph still works
pytest tests/test_graph.py
```

## Migration

Existing deployments can:

1. **Opt-in**: Set `use_dual_loop=True` in config (default)
2. **Gradual Rollout**: Test with specific task types first
3. **Fallback**: Keep original graph available (`use_dual_loop=False`)
4. **Monitor**: Check `decisions.jsonl` for verification scores

## Conclusion

The dual-loop architecture transforms Progenitor from a linear task processor into an iterative quality-focused agent that:

- Defines success before starting
- Verifies outputs objectively
- Learns from each iteration
- Stops when quality is achieved

This implementation of loop.spec.md provides the foundation for building reliable, high-quality AI agents that meet user expectations consistently.
