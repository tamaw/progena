---
title: Clarification incorporation
description: Updates the ideal state with a user's clarification.
variables: [clarification, current_state, original_task, trigger]
---
# Ideal State Clarification Incorporation

You incorporate user clarifications into task specifications. Be specific and actionable.

## Original Task

{original_task}

## What Triggered Clarification

{trigger}

## User's Clarification

{clarification}

## Current Ideal State

{current_state}

## Instructions

Based on the user's clarification, determine how the ideal state should be updated:

### Updated Task Description
- Provide an enhanced task description that incorporates the clarification
- Keep the original intent but add the new understanding
- Leave empty if no change is needed

### New Success Criteria
- Add criteria that address requirements revealed by the clarification
- Use unique IDs prefixed with "criterion_clarified_"

### New Anti-Criteria
- Add anti-criteria for things the user explicitly wants to avoid
- Use unique IDs prefixed with "anti_clarified_"

### Additional Constraints
- Any new hard constraints from the clarification

### Reasoning
- Explain how the clarification changes the task understanding
