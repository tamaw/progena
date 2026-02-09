"""
Quick test script for LiteLLM /responses endpoint integration.
"""

import asyncio
import os
from pathlib import Path

# Add app directory to path
import sys
sys.path.insert(0, str(Path(__file__).parent / "app"))

from progenitor.config import LLMConfig
from progenitor.llm.client import LLMClient


async def test_responses_endpoint():
    """Test the /responses endpoint with a simple query."""
    import logging
    logging.basicConfig(level=logging.DEBUG)

    print("Testing LiteLLM /responses endpoint...")

    # Create config
    config = LLMConfig.from_env()
    print(f"Using endpoint: {config.base_url}")
    print(f"Model: {config.model_default}")
    print(f"API Key: {config.api_key[:20] if config.api_key else 'None'}...")

    # Create client
    client = LLMClient(config=config)

    try:
        # Test simple query
        messages = [{"role": "user", "content": "Say 'Hello from LiteLLM /responses!' in a friendly way."}]
        print("\nSending test request...")
        print(f"Endpoint URL: {client.endpoint_url}")

        response = await client.chat(messages)

        print(f"\nGot response, type: {type(response)}")

        print("\n[SUCCESS]")
        print(f"Response type: {type(response)}")
        print(f"Response length: {len(response) if isinstance(response, str) else 'N/A'}")

        # Write to file to avoid Windows console encoding issues
        with open("test_response.txt", "w", encoding="utf-8") as f:
            f.write(f"Type: {type(response)}\n")
            f.write(f"Content:\n{response}")

        print("Response written to test_response.txt")

        # Basic validation
        if isinstance(response, str) and len(response) > 0:
            print("Validation: PASS - Got non-empty string response")
            return True
        else:
            print("Validation: FAIL - Expected non-empty string response")
            return False
    except Exception as e:
        print(f"\n[ERROR]: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        await client.close()


if __name__ == "__main__":
    success = asyncio.run(test_responses_endpoint())
    sys.exit(0 if success else 1)
