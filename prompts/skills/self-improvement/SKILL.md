---
name: self-improvement
description: Self-reflection, learning from build iterations, extracting patterns, and continuous improvement of agent capabilities
allowed-tools:
  - read_file
  - write_file
  - shell
---

# Self-Improvement Skill

## When to Use
- After a build iteration completes (learn phase)
- User asks for self-reflection
- Patterns need to be extracted from past successes/failures
- Agent capabilities need assessment

## Iteration Analysis Framework
After each build iteration, analyze:

1. **Was the experiment plan achieved?** Compare planned vs actual outcome
2. **Are we progressing toward the ideal state?** Gap analysis
3. **What worked?** Identify successful patterns with confidence scores
4. **What failed?** Identify anti-patterns and root causes
5. **What adjustments to make?** Prioritized, actionable next steps

## Lesson Format
Each lesson should include:
- **Pattern**: What was observed
- **Confidence**: How reliable (0.0-1.0) based on repetition
- **Category**: code_quality, testing, architecture, performance, user_interaction
- **Action**: Concrete adjustment for next iteration

## Anti-Criteria Learning
Track conditions that indicate BAD solutions:
- "If the solution requires X, it's probably wrong because..."
- These are as valuable as positive criteria for convergence

## Storing Lessons
Lessons are stored in `/memories/patterns.txt` and persist across sessions.
High-confidence patterns (>0.8) should be promoted to skill instructions.

## File Analysis
When reflecting on source code:
1. Summarize main purpose
2. Identify key components and their roles
3. Note capabilities provided
4. Map relationships to other modules
