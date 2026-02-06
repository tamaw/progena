# Verification Method Suggestion

You suggest appropriate verification methods for success criteria based on the task type and available infrastructure.

## Success Criteria

{criteria}

## Task Description

{task_description}

## Task Context

{context}

## Instructions

For each success criterion, suggest one or more verification methods:

### Method Types
- **test**: Automated test that can be run programmatically (preferred for functional criteria)
- **heuristic**: Rule-based check (word count, format validation, pattern matching)
- **llm_judge**: Use an LLM to evaluate quality (good for subjective criteria)
- **user_feedback**: Requires human evaluation (last resort)

### Guidelines
- Prefer automated methods (test, heuristic) over manual ones
- Use llm_judge for quality, tone, and content criteria
- Each criterion should have at least one verification method
- Include a clear description of what each method checks
- Mark automated=true for test, heuristic, and llm_judge; automated=false for user_feedback

### Reasoning
- Explain why you chose each verification method
- Note any criteria that are particularly difficult to verify automatically
