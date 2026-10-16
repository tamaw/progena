---
title: Copilot instruction for an agent
description: Produces a focused Copilot instruction for building an agent.
---
Given this build task, generate a focused instruction for Copilot.

TASK: {task_description}
AGENT NAME: {entity_name}
SPEC FILE LOCATION: {spec_rel_path}

Generate a concise, actionable prompt (2-4 sentences) that:
1. References the spec file for details
2. Emphasizes the key behavior/capability the agent should have
3. Mentions any critical implementation details

Output ONLY the prompt text, nothing else:
