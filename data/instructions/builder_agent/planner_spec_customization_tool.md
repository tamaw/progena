---
title: Tool spec customisation
description: Customises the tool structure template into a spec for the requested tool.
---
You are generating a build specification for a FastMCP tool.

TASK FROM USER: {task_description}

TOOL NAME: {entity_name}

TEMPLATE TO CUSTOMIZE:
{template_content}

Generate a COMPLETE, DETAILED build specification that:
1. Replaces {{tool_name}} with "{entity_name}"
2. Replaces {{tool_class}} with the PascalCase version of the tool name
3. Replaces {{primary_function}} with the main function name based on the task
4. Adds specific implementation details based on the user's task
5. Includes the exact behavior the tool should have
6. Specifies what each @mcp.tool() function should return

For this task "{task_description}", specify:
- EXACTLY what the tool should do
- Input parameters and their types
- Return value format
- Error handling behavior

Output the complete customized specification markdown:
