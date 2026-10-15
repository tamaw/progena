---
title: Anti-criteria from violations
description: Generalises observed violations into anti-criteria that prevent recurrence.
variables: [existing_anti_criteria, task_description, violations]
---
# Anti-Criteria Extraction from Violations

You analyze observed violations and extract generalized anti-criteria to prevent recurrence.

## Observed Violations

{violations}

## Existing Anti-Criteria

{existing_anti_criteria}

## Task Context

{task_description}

## Instructions

Analyze the violations and generate anti-criteria:

### Pattern Analysis
- Identify patterns across violations (recurring themes, root causes)
- Don't just restate individual violations -- generalize them

### Anti-Criteria Generation
- Create anti-criteria that would catch similar violations in the future
- Use unique IDs prefixed with "anti_violation_"
- Set severity based on the impact of the violations (0.0-2.0)
- Write specific detection methods that can be automated if possible
- Do NOT duplicate existing anti-criteria

### Reasoning
- Explain the patterns you identified and why each anti-criterion was created
