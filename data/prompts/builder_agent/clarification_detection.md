Analyze this Copilot CLI output and determine if clarification from the user is needed.

## Original Task
{task_description}

{ideal_state_summary}

## Copilot Output (last 2000 chars)
{output_tail}

## Heuristic Detection
Detected need: {heuristic_needs}
Confidence: {heuristic_confidence}
Context: {heuristic_context}

## Your Analysis

Determine:
1. Does the output indicate Copilot needs more information from the user? (yes/no)
2. If yes, what specific information is needed?
3. How should we ask the user? (Format a clear, concise question)
4. Category of clarification: requirements, technical, design, scope, or other
5. Confidence level (0.0-1.0)

Respond in this exact format:
NEEDS_CLARIFICATION: yes/no
CONFIDENCE: 0.X
CATEGORY: category_name
CONTEXT: Brief description of what triggered this
QUESTION: The question to ask the user (if clarification needed)
