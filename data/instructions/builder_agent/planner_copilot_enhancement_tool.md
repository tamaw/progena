---
title: Copilot instruction for a tool
description: Produces a focused Copilot instruction for building a FastMCP tool.
---
Given this build task, generate a focused instruction for Copilot to build a FastMCP tool.

TASK: {task_description}
TOOL NAME: {entity_name}
SPEC FILE LOCATION: {spec_rel_path}

Generate a concise, actionable prompt (2-4 sentences) that:
1. References the spec file for details
2. Emphasizes the key functionality the tool should provide via @mcp.tool decorators
3. Mentions any critical input/output requirements

Output ONLY the prompt text, nothing else:
