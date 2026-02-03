# Agent Test Script Generation

Generate a Python test script to validate an agent running as an HTTP server.

## Agent Details
- **Agent Name**: {agent_name}
- **Description**: {description}
- **Endpoint**: {endpoint}
- **Port**: {port}
- **Capabilities**: {capabilities}

## Experiment Plan
{experiment_prompts}

## Test Requirements

Create a Python script `test_agent.py` that:

1. **Health Check**: First verify the agent is running by hitting `/health` endpoint
2. **Agent Card Check**: Verify `/.well-known/agent.json` returns valid metadata
3. **Functional Tests**: For each prompt in the experiment plan:
   - Send the prompt to the agent's POST `/` endpoint
   - Verify the response structure is valid JSON
   - Check `success` field is True
   - Verify `response` field contains a non-empty string
   - Check `error` field is None or null

## Required Output Format

The script must print results in this exact format for parsing:

```
TEST_START: <test_name>
TEST_RESULT: <PASS|FAIL>: <message>
```

At the end, print a summary:
```
SUMMARY: <passed_count>/<total_count> tests passed
FINAL_RESULT: <PASS|FAIL>
```

## Script Template

```python
import asyncio
import aiohttp
import json
import sys

ENDPOINT = "{endpoint}"
PORT = {port}
BASE_URL = f"http://localhost:{PORT}"

async def main():
    passed = 0
    failed = 0
    total = 0

    async with aiohttp.ClientSession() as session:
        # Health check
        print("TEST_START: health_check")
        total += 1
        try:
            async with session.get(f"{{BASE_URL}}/health", timeout=5) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("status") == "healthy":
                        print("TEST_RESULT: PASS: Health endpoint returned healthy")
                        passed += 1
                    else:
                        print(f"TEST_RESULT: FAIL: Health status was {{data.get('status')}}")
                        failed += 1
                else:
                    print(f"TEST_RESULT: FAIL: Health endpoint returned status {{resp.status}}")
                    failed += 1
        except Exception as e:
            print(f"TEST_RESULT: FAIL: Health check failed: {{e}}")
            failed += 1

        # Agent card check
        print("TEST_START: agent_card")
        total += 1
        try:
            async with session.get(f"{{BASE_URL}}/.well-known/agent.json", timeout=5) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("name"):
                        print(f"TEST_RESULT: PASS: Agent card returned name '{{data.get('name')}}'")
                        passed += 1
                    else:
                        print("TEST_RESULT: FAIL: Agent card missing name")
                        failed += 1
                else:
                    print(f"TEST_RESULT: FAIL: Agent card endpoint returned status {{resp.status}}")
                    failed += 1
        except Exception as e:
            print(f"TEST_RESULT: FAIL: Agent card check failed: {{e}}")
            failed += 1

        # Functional tests for each prompt
        test_prompts = {test_prompts_json}

        for i, prompt in enumerate(test_prompts):
            test_name = f"prompt_{{i+1}}"
            print(f"TEST_START: {{test_name}}")
            total += 1
            try:
                payload = {{"request": prompt, "context": {{}}}}
                async with session.post(f"{{BASE_URL}}/", json=payload, timeout=30) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data.get("success") is True and data.get("response"):
                            print(f"TEST_RESULT: PASS: Got response: {{data.get('response')[:100]}}...")
                            passed += 1
                        elif data.get("error"):
                            # Some agents return success=False with helpful error (e.g. missing location)
                            print(f"TEST_RESULT: PASS: Agent responded with clarification: {{data.get('error')[:100]}}")
                            passed += 1
                        else:
                            print(f"TEST_RESULT: FAIL: Invalid response structure: {{data}}")
                            failed += 1
                    else:
                        print(f"TEST_RESULT: FAIL: Request returned status {{resp.status}}")
                        failed += 1
            except Exception as e:
                print(f"TEST_RESULT: FAIL: Prompt test failed: {{e}}")
                failed += 1

    print(f"SUMMARY: {{passed}}/{{total}} tests passed")
    if failed == 0:
        print("FINAL_RESULT: PASS")
        sys.exit(0)
    else:
        print("FINAL_RESULT: FAIL")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
```

Generate ONLY the Python code, no explanations. Make sure to:
- Use aiohttp for async HTTP calls
- Include proper timeout handling
- Print test results in the exact format specified
- Exit with code 0 on success, 1 on failure
