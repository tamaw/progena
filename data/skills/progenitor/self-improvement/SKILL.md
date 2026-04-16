---
name: self-improvement
description: Reflect on build iterations, extract reusable patterns, and improve agent behavior over time. Use when reviewing completed work, capturing lessons learned, or updating long-term agent capabilities.
allowed-tools: read_file write_file shell
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
Store lessons as category-based Markdown files under `/memories/` so they stay
easy to scan with `ls`, `glob`, `grep`, and `read_file`.

- `/memories/patterns/code_quality.md`
- `/memories/patterns/testing.md`
- `/memories/patterns/architecture.md`
- `/memories/patterns/performance.md`
- `/memories/patterns/user_interaction.md`
- `/memories/anti-patterns.md`

These files persist across sessions and restarts.
High-confidence patterns (>0.8) should be promoted to skill instructions.

Before writing a new lesson:
1. Read the relevant category file first.
2. Merge with an existing lesson when it describes the same pattern.
3. Prefer short Markdown bullets with pattern, confidence, and action.
4. Put anti-patterns in `/memories/anti-patterns.md` instead of mixing them
   into positive pattern files.

## Memory Compaction
Do not let memory files grow forever.

1. When a category file becomes repetitive or hard to skim, rewrite it into a
   compact summary instead of appending more bullets.
2. Keep durable high-confidence lessons and the most useful recent examples.
3. Remove duplicates, stale low-confidence notes, and wording that no longer
   helps future iterations.
4. Preserve concise anti-patterns separately in `/memories/anti-patterns.md`.

## File Analysis
When reflecting on source code:
1. Summarize main purpose
2. Identify key components and their roles
3. Note capabilities provided
4. Map relationships to other modules
