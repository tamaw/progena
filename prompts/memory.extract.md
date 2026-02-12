You are extracting a durable memory from a user's request.

User request:
{user_input}

Return JSON with these fields:
- summary: short, durable fact or preference (string)
- details: longer detail (string)
- tags: array of lowercase tags
- confidence: number between 0 and 1

If there is nothing worth storing, return:
{"skip": true, "reason": "<short reason>"}
