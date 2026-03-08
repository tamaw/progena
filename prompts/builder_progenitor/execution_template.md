# Builder Execution Prompt Template

This file defines the execution prompt template used during task execution.

The actual prompt is built dynamically with:
- User request (the original task)
- Execution context (metadata, repository state)
- Plan summary (high-level objectives)
- Steps (detailed plan steps with commands)
- Risks (identified risks to mitigate)
- Reasoning (analysis of why this plan was chosen)

## Prompt Format

```
User request:
{request_text}

Execution context:
{context_json}

Plan summary: {plan_summary}
Steps:
{steps}
Risks:
{risks}
Reasoning: {reasoning}

Execute the plan using the available tools and return a concise final report.
```

The template is assembled in code and sent to the LLM for execution.
