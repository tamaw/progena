"""
Quick test script for LiteLLM /responses endpoint integration.
Tests both regular chat and structured output with JSON schema.
"""

import asyncio
import json
import sys
from io import TextIOWrapper
from typing import cast
from pathlib import Path

# Fix Windows console encoding for Unicode characters
if sys.platform == "win32":
    try:
        stdout = cast(TextIOWrapper, sys.stdout)
        stderr = cast(TextIOWrapper, sys.stderr)
        stdout.reconfigure(encoding='utf-8')
        stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass  # Fall back to default encoding

from pydantic import BaseModel, Field

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.progenitor.config import LLMConfig
from app.progenitor.llm.client import LLMClient, _build_json_schema_format


class GreetingResponse(BaseModel):
    """Structured greeting response."""
    greeting: str = Field(description="A friendly greeting message")
    emoji: str = Field(description="An appropriate emoji")
    enthusiasm_level: int = Field(description="Enthusiasm level from 1-10", ge=1, le=10)


class PersonInfo(BaseModel):
    """Test structured output for person information."""
    name: str = Field(description="Person's name")
    age: int = Field(description="Person's age")
    occupation: str = Field(description="Person's occupation")
    hobbies: list[str] = Field(description="List of hobbies")


async def test_basic_chat():
    """Test the /responses endpoint with a simple query."""
    print("=" * 60)
    print("TEST 1: Basic Chat (No Structured Output)")
    print("=" * 60)

    config = LLMConfig.from_env()
    client = LLMClient(config=config)

    try:
        messages = [{"role": "user", "content": "Say 'Hello from LiteLLM /responses!' in a friendly way."}]
        print(f"Sending request to: {client.endpoint_url}")

        response = await client.chat(messages)

        print("\n✓ Got response")
        print(f"  Type: {type(response)}")
        print(f"  Length: {len(response)} chars")
        print(f"  Content: {response[:100]}..." if len(response) > 100 else f"  Content: {response}")

        if isinstance(response, str) and len(response) > 0:
            print("\n[PASS] Basic chat test")
            return True
        else:
            print("\n[FAIL] Expected non-empty string response")
            return False
    except Exception as e:
        print(f"\n[ERROR]: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        await client.close()


async def test_structured_output():
    """Test structured output with JSON schema."""
    print("\n" + "=" * 60)
    print("TEST 2: Structured Output (JSON Schema)")
    print("=" * 60)

    config = LLMConfig.from_env()
    client = LLMClient(config=config)

    try:
        # Build response format using the function we're testing
        response_format = _build_json_schema_format(GreetingResponse, "greeting_response")
        schema = response_format["json_schema"]["schema"]

        # Verify schema builder enforcement
        expected_required = set(schema.get("properties", {}).keys())
        if schema.get("additionalProperties") is not False:
            raise AssertionError("Schema builder did not set additionalProperties=false")
        if expected_required and set(schema.get("required", [])) != expected_required:
            raise AssertionError("Schema builder did not require all properties")

        print(f"Response format type: {response_format['type']}")
        print(f"Schema name: {response_format['json_schema']['name']}")
        print(f"Schema strict: {response_format['json_schema']['strict']}")
        print("\nFull schema:")
        print(json.dumps(response_format, indent=2))

        messages = [{"role": "user", "content": "Generate a friendly greeting with an emoji and rate your enthusiasm from 1-10."}]

        print("\nSending structured output request...")
        response_text = await client.chat(messages, response_format=response_format)

        print(f"\n✓ Got response (length: {len(response_text)} chars)")
        print(f"Response text: {response_text[:200]}...")

        # Try to parse as JSON
        try:
            response_json = json.loads(response_text)
            validated = GreetingResponse.model_validate(response_json)

            print("\n✓ Validated structured response:")
            print(f"  Greeting: {validated.greeting}")
            print(f"  Emoji: {validated.emoji}")
            print(f"  Enthusiasm: {validated.enthusiasm_level}/10")

            # Verify all required fields are present
            if all([validated.greeting, validated.emoji, 1 <= validated.enthusiasm_level <= 10]):
                print("\n[PASS] Structured output test")
                return True
            else:
                print("\n[FAIL] Validation failed")
                return False
        except json.JSONDecodeError as e:
            print(f"\n[FAIL] Response is not valid JSON: {e}")
            print(f"Response was: {response_text}")
            return False

    except Exception as e:
        print(f"\n[ERROR]: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        await client.close()


async def test_complex_structured_output():
    """Test complex structured output with nested fields."""
    print("\n" + "=" * 60)
    print("TEST 3: Complex Structured Output (Nested Schema)")
    print("=" * 60)

    config = LLMConfig.from_env()
    client = LLMClient(config=config)

    try:
        response_format = _build_json_schema_format(PersonInfo, "person_info")
        schema = response_format["json_schema"]["schema"]
        schema["additionalProperties"] = False
        if "properties" in schema:
            schema["required"] = list(schema["properties"].keys())

        messages = [{
            "role": "user",
            "content": "Create a fictional person profile with name, age, occupation, and a list of 3 hobbies."
        }]

        print("Sending complex structured output request...")
        response_text = await client.chat(messages, response_format=response_format)

        print(f"\n✓ Got response (length: {len(response_text)} chars)")
        print(f"Response text: {response_text[:200]}...")

        # Try to parse as JSON
        try:
            response_json = json.loads(response_text)
            validated = PersonInfo.model_validate(response_json)

            print("\n✓ Validated complex structured response:")
            print(f"  Name: {validated.name}")
            print(f"  Age: {validated.age}")
            print(f"  Occupation: {validated.occupation}")
            print(f"  Hobbies: {', '.join(validated.hobbies)}")

            # Verify all fields are present and valid
            if all([
                validated.name,
                validated.age > 0,
                validated.occupation,
                len(validated.hobbies) >= 1
            ]):
                print("\n[PASS] Complex structured output test")
                return True
            else:
                print("\n[FAIL] Validation failed")
                return False
        except json.JSONDecodeError as e:
            print(f"\n[FAIL] Response is not valid JSON: {e}")
            print(f"Response was: {response_text}")
            return False

    except Exception as e:
        print(f"\n[ERROR]: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        await client.close()


async def test_minimal_json_schema():
    """Test a minimal custom json_schema response_format."""
    print("\n" + "=" * 60)
    print("TEST 4: Minimal JSON Schema (Ping)")
    print("=" * 60)

    config = LLMConfig.from_env()
    client = LLMClient(config=config)

    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": "Ping",
            "schema": {
                "type": "object",
                "properties": {"ok": {"type": "boolean"}},
                "required": ["ok"],
                "additionalProperties": False,
            },
            "strict": True,
        },
    }

    try:
        messages = [{"role": "user", "content": "Return {\"ok\": true}."}]
        print("Sending minimal schema request...")
        response_text = await client.chat(messages, response_format=response_format)

        print(f"\n✓ Got response (length: {len(response_text)} chars)")
        print(f"Response text: {response_text}")

        response_json = json.loads(response_text)
        ok_value = response_json.get("ok")

        if isinstance(ok_value, bool):
            print("\n[PASS] Minimal schema test")
            return True

        print("\n[FAIL] 'ok' is not a boolean")
        return False
    except Exception as e:
        print(f"\n[ERROR]: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        await client.close()


async def run_all_tests():
    """Run all tests and report results."""
    import logging
    logging.basicConfig(level=logging.INFO)

    print("\n" + "=" * 60)
    print("LiteLLM /responses Endpoint Test Suite")
    print("=" * 60)

    config = LLMConfig.from_env()
    print("\nConfiguration:")
    print(f"  Endpoint: {config.base_url}")
    print(f"  Model: {config.model_default}")
    # Never print secrets in diagnostics (even partial prefixes can leak).
    print(f"  API Key: {'<set>' if config.api_key else '<missing>'}")
    print()

    results = []

    # Run all tests
    results.append(("Basic Chat", await test_basic_chat()))
    results.append(("Structured Output", await test_structured_output()))
    results.append(("Complex Structured Output", await test_complex_structured_output()))
    results.append(("Minimal JSON Schema", await test_minimal_json_schema()))

    # Summary
    print("\n" + "=" * 60)
    print("Test Summary")
    print("=" * 60)

    for test_name, passed in results:
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"{status} - {test_name}")

    total_passed = sum(1 for _, passed in results if passed)
    total_tests = len(results)

    print(f"\nTotal: {total_passed}/{total_tests} tests passed")

    return all(passed for _, passed in results)


if __name__ == "__main__":
    success = asyncio.run(run_all_tests())
    sys.exit(0 if success else 1)
