Analyze this iteration and determine:

1. **Was the EXPERIMENT PLAN achieved?** Compare what was planned vs what happened.
2. **Are we progressing toward the IDEAL STATE?** Compare current state vs target.
3. **What worked, what failed, and what adjustments to make?**

## Iteration Context

**Iteration:** {iteration}

**User Request:**
{user_request}

## Ideal State (our target)

{ideal_state}

## Experiment Plan (what we planned this iteration)

{experiment_plan}

## Think Phase (reasoning and approaches)

{think_phase}

## Execution Result (what actually happened)

{execution_result}

## Verification Report (how well we did)

{verification_report}

## Previous Lessons (from earlier iterations)

{previous_lessons}

## Best Result So Far

{best_result}

## Analysis Instructions

Be SPECIFIC and ACTIONABLE. Focus on root causes, not symptoms.
If there was a build/test failure, identify it clearly.
If we're making progress, acknowledge what's working.

### Confidence Assessment

For each lesson in `detailed_lessons`, provide a confidence score (0.0-1.0) based on the strength of evidence. Include concrete evidence from the execution result or verification report.

### Adjustment Prioritization

For each adjustment in `prioritized_adjustments`, assign:
- **priority**: high (blocks progress), medium (improves quality), low (nice to have)
- **impact_score**: 0.0-1.0 expected impact if implemented
- **category**: architecture, implementation, testing, configuration, or general

### Pattern Detection

Analyze the previous lessons for recurring themes across iterations:
- **recurring_issue**: Same problem appearing across iterations
- **improvement_trend**: Consistent progress in a specific area
- **stagnation_signal**: No progress despite repeated attempts
- **regression_pattern**: Getting worse in a specific area

### Action-Outcome Correlation

For each action in the experiment plan, map what actually happened:
- Which planned actions led to positive outcomes?
- Which planned actions led to negative outcomes?
- Which planned actions had no measurable effect?
