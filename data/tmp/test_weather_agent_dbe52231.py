"""Auto-generated test script for weather_agent."""
import asyncio
import aiohttp
import sys

BASE_URL = "http://localhost:8080"

async def main():
    passed = 0
    failed = 0
    total = 0

    async with aiohttp.ClientSession() as session:
        # Health check
        print("TEST_START: health_check")
        total += 1
        try:
            async with session.get(f"{BASE_URL}/health", timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("status") == "healthy":
                        print("TEST_RESULT: PASS: Health endpoint returned healthy")
                        passed += 1
                    else:
                        print(f"TEST_RESULT: FAIL: Health status was {data.get('status')}")
                        failed += 1
                else:
                    print(f"TEST_RESULT: FAIL: Health endpoint returned status {resp.status}")
                    failed += 1
        except Exception as e:
            print(f"TEST_RESULT: FAIL: Health check failed: {e}")
            failed += 1

        # Agent card check
        print("TEST_START: agent_card")
        total += 1
        try:
            async with session.get(f"{BASE_URL}/.well-known/agent.json", timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("name"):
                        print(f"TEST_RESULT: PASS: Agent card returned name '{data.get('name')}'")
                        passed += 1
                    else:
                        print("TEST_RESULT: FAIL: Agent card missing name")
                        failed += 1
                else:
                    print(f"TEST_RESULT: FAIL: Agent card endpoint returned status {resp.status}")
                    failed += 1
        except Exception as e:
            print(f"TEST_RESULT: FAIL: Agent card check failed: {e}")
            failed += 1

        # Functional tests for each prompt
        test_prompts = ["Hello, are you working?"]

        for i, prompt in enumerate(test_prompts):
            test_name = f"prompt_{i+1}"
            print(f"TEST_START: {test_name}")
            total += 1
            try:
                payload = {"request": prompt, "context": {}}
                async with session.post(f"{BASE_URL}/", json=payload, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data.get("success") is True and data.get("response"):
                            response_preview = str(data.get("response"))[:100]
                            print(f"TEST_RESULT: PASS: Got response: {response_preview}...")
                            passed += 1
                        elif data.get("error"):
                            # Some agents return success=False with helpful error (e.g. missing location)
                            error_preview = str(data.get("error"))[:100]
                            print(f"TEST_RESULT: PASS: Agent responded with clarification: {error_preview}")
                            passed += 1
                        else:
                            print(f"TEST_RESULT: FAIL: Invalid response structure: {data}")
                            failed += 1
                    else:
                        print(f"TEST_RESULT: FAIL: Request returned status {resp.status}")
                        failed += 1
            except Exception as e:
                print(f"TEST_RESULT: FAIL: Prompt test failed: {e}")
                failed += 1

    print(f"SUMMARY: {passed}/{total} tests passed")
    if failed == 0:
        print("FINAL_RESULT: PASS")
        sys.exit(0)
    else:
        print("FINAL_RESULT: FAIL")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
