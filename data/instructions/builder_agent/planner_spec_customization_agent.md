---
title: Agent spec customisation
description: Customises the agent structure template into a spec for the requested agent.
variables: [agent_class, agent_name, entity_name, task_description, template_content]
---
You are generating a build specification for an AI agent.

TASK FROM USER: {task_description}

AGENT NAME: {entity_name}

TEMPLATE TO CUSTOMIZE:
{template_content}

Generate a COMPLETE, DETAILED build specification that:
1. Replaces {{agent_name}} with "{entity_name}"
2. Replaces {{agent_class}} with the PascalCase version of the agent name
3. Adds specific implementation details based on the user's task
4. Includes the exact behavior the agent should have
5. Specifies what the agent's invoke() method should return for different inputs

For this task "{task_description}", specify:
- EXACTLY what the agent should respond with
- Any specific text/format requirements
- How it should handle various inputs

Output the complete customized specification markdown:
