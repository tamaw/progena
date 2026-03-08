#!/usr/bin/env python
"""
Invoke the builder_progenitor agent via A2A JSON-RPC protocol.

Usage:
    python scripts/invoke_builder.py "Create a LICENSE.md with MIT license"
    python scripts/invoke_builder.py "Fix linting errors in app/progenitor" --endpoint "http://localhost:8108"
"""

import asyncio
import json
import sys
import uuid
from typing import Any

import httpx


async def invoke_builder(
    request: str,
    endpoint: str = "http://localhost:8108",
    timeout: float = 300.0,
) -> dict[str, Any]:
    """Invoke builder_progenitor agent via A2A JSON-RPC.
    
    Args:
        request: The task description (e.g., "Create a LICENSE.md file")
        endpoint: Builder agent HTTP endpoint
        timeout: Request timeout in seconds
        
    Returns:
        Response dict with result or error
    """
    message_id = uuid.uuid4().hex
    
    # A2A JSON-RPC payload for message/send
    payload = {
        "jsonrpc": "2.0",
        "id": uuid.uuid4().hex,
        "method": "message/send",
        "params": {
            "message": {
                "kind": "message",
                "message_id": message_id,
                "role": "user",
                "parts": [{"kind": "text", "text": request}],
            },
        },
    }
    
    print(f"🚀 Invoking builder_progenitor at {endpoint}")
    print(f"📝 Request: {request}")
    print()
    
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                endpoint,
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            
            if response.status_code != 200:
                print(f"❌ HTTP {response.status_code}")
                print(response.text[:500])
                return {"success": False, "error": f"HTTP {response.status_code}"}
            
            data = response.json()
            
            # Check for JSON-RPC error
            if "error" in data and data["error"]:
                error = data["error"]
                print(f"❌ A2A Error: {error}")
                return {"success": False, "error": error}
            
            # Success
            result = data.get("result")
            print(f"✅ Builder agent invoked successfully")
            print(f"📤 Response:")
            print(json.dumps(result, indent=2)[:1000])
            return {"success": True, "result": result}
            
    except httpx.ConnectError as e:
        print(f"❌ Connection error: {e}")
        print(f"   Is the builder agent running at {endpoint}?")
        return {"success": False, "error": str(e)}
    except httpx.TimeoutException:
        print(f"⏱️  Request timed out after {timeout}s (builder may still be processing)")
        return {"success": False, "error": f"timeout after {timeout}s"}
    except Exception as e:
        print(f"❌ Error: {e}")
        return {"success": False, "error": str(e)}


async def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/invoke_builder.py <request> [--endpoint <url>]")
        print()
        print("Example:")
        print('  python scripts/invoke_builder.py "Create a LICENSE.md with MIT license"')
        sys.exit(1)
    
    request = sys.argv[1]
    endpoint = "http://localhost:8108"
    
    # Parse optional --endpoint flag
    if "--endpoint" in sys.argv:
        idx = sys.argv.index("--endpoint")
        if idx + 1 < len(sys.argv):
            endpoint = sys.argv[idx + 1]
    
    result = await invoke_builder(request, endpoint)
    sys.exit(0 if result.get("success") else 1)


if __name__ == "__main__":
    asyncio.run(main())
