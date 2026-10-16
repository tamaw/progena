---
title: Success criteria validation
description: Checks that success criteria are well formed, measurable and sufficient.
---
# Success Criteria Validation

You validate whether a set of success criteria is well-formed, measurable, and sufficient for guiding an iterative agent loop.

## Criteria to Validate

{criteria}

## Anti-Criteria

{anti_criteria}

## Task Description

{task_description}

## Instructions

Evaluate the criteria set for quality:

### Check Each Criterion For
- **Specificity**: Is it specific enough to measure? (not vague like "good quality")
- **Measurability**: Can the measurement actually produce a score?
- **Actionability**: Can an agent act on this to improve?
- **Conflicts**: Does it conflict with other criteria or anti-criteria?
- **Redundancy**: Is it duplicating another criterion?

### Check the Overall Set For
- **Completeness**: Are there obvious gaps in what's being measured?
- **Balance**: Is there a good mix of criterion types?
- **Feasibility**: Are the target scores achievable?

### Output
- Set is_valid to false if there are critical issues that would prevent the loop from working
- Provide actionable suggested_fix for each issue
- Give an overall quality score (0.0-1.0) for the criteria set
