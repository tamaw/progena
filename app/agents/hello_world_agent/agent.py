import os
import time
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI()
_start_time = time.time()


class HelloWorldAgent:
    AGENT_ID = "hello_world_agent"
    AGENT_NAME = "Hello World Agent"
    DESCRIPTION = "Responds with 'Hello World!' to any request."
    CAPABILITIES = ["hello_world", "static_response"]

    async def process(self, _request: str, _context: dict[str, Any]) -> str:
        return "Hello World!"


agent = HelloWorldAgent()


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@app.on_event("startup")
async def startup_event() -> None:
    port = int(os.environ.get("PORT", "8080"))
    print("STATUS:starting:Initializing Hello World Agent")
    print(f"STATUS:running:Server started on port {port}")
    print("STATUS:waiting:Idle, waiting for requests")


@app.on_event("shutdown")
async def shutdown_event() -> None:
    print("STATUS:stopping:Shutting down")
    print("STATUS:stopped:Agent terminated")


@app.get("/.well-known/agent.json")
async def agent_card() -> JSONResponse:
    port = int(os.environ.get("PORT", "8080"))
    payload = {
        "name": agent.AGENT_NAME,
        "description": agent.DESCRIPTION,
        "version": "0.1.0",
        "protocol": "a2a",
        "capabilities": agent.CAPABILITIES,
        "endpoint": f"http://localhost:{port}",
    }
    return JSONResponse(content=payload)


@app.get("/health")
async def health() -> JSONResponse:
    uptime = time.time() - _start_time
    payload = {
        "status": "healthy",
        "name": agent.AGENT_NAME,
        "version": "0.1.0",
        "uptime_seconds": float(uptime),
        "timestamp": _utc_now_iso(),
    }
    return JSONResponse(content=payload)


@app.post("/")
async def handle(req: Request) -> JSONResponse:
    try:
        body = await req.json()
    except Exception:
        body = {}
    req_str = body.get("request", "") if isinstance(body, dict) else ""
    ctx = body.get("context", {}) if isinstance(body, dict) else {}

    print("STATUS:executing:Processing request")
    try:
        result = await agent.process(req_str, ctx)
        response = {
            "success": True,
            "response": result,
            "error": None,
            "metadata": {},
        }
        print("STATUS:waiting:Idle, waiting for requests")
        return JSONResponse(content=response)
    except Exception:
        print("STATUS:failed:Error processing request")
        err: dict[str, Any] = {
            "success": False,
            "response": None,
            "error": "Internal error",
            "metadata": {},
        }
        return JSONResponse(content=err, status_code=500)
