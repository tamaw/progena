---
title: Ideal state extraction
description: Decomposes a task into measurable success criteria and anti-criteria.
---
# Ideal State Extraction

You are an expert at analyzing tasks and defining measurable success criteria. Your job is to decompose a task into a structured ideal state that guides an iterative agent loop.

## Task to Analyze

{task}

## Additional Context

{context}

## Instructions

Analyze the task and produce a structured ideal state with:

### Success Criteria
- Create 2-5 ranked, measurable criteria
- Each must have a clear measurement approach (not vague)
- Assign appropriate types: functional, quality, performance, security, maintainability, user_experience
- Rank by priority (1 = highest)
- Set realistic target scores (0.0-1.0)
- Use unique IDs like "criterion_1", "criterion_2"

### Anti-Criteria
- Identify 1-3 things to actively avoid
- Common failure modes, user annoyances, quality violations
- Assign severity (0.0-2.0) based on impact
- Include specific detection methods
- Use unique IDs like "anti_1", "anti_2"

### Quality Bar
- Define what "euphoric surprise" looks like for this specific task
- Give concrete examples of quality output
- Give concrete anti-examples of what quality is NOT

### Verification Methods
- For each success criterion, suggest a verification approach
- Types: test (automated), heuristic (rule-based), llm_judge (LLM evaluation), user_feedback
- Prefer automated methods where possible

### Stop Conditions
- Always include min_score (typically 0.8) and max_iterations (typically 5)
- Add time_limit or tool_budget if appropriate

### Constraints and Assumptions
- Hard constraints the solution must respect
- Working assumptions that guide the approach
