import asyncio
import json
import os
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class BtcPriceAgent:
    AGENT_ID = "btc_price_agent"
    AGENT_NAME = "BTC Price Agent"
    DESCRIPTION = "Returns the current Bitcoin (BTC) price in USD using live market data."
    CAPABILITIES = [
        "get_btc_price",
        "crypto_price_lookup",
        "btc_usd_spot_price",
    ]

    async def process(self, _request: str, _context: dict[str, Any]) -> dict[str, Any]:
        """Ignore inputs and always return current BTC price in USD.

        Raises Exception on failure.
        """
        # Try CoinGecko first
        try:
            price = await asyncio.to_thread(fetch_coingecko_price)
            source = "CoinGecko"
        except Exception:
            # Fallback to Coinbase
            try:
                price = await asyncio.to_thread(fetch_coinbase_price)
                source = "Coinbase"
            except Exception:
                raise Exception("Failed to retrieve BTC price from market data provider")

        timestamp = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        return {
            "asset": "BTC",
            "currency": "USD",
            "price": float(price),
            "source": source,
            "timestamp": timestamp,
        }


# Helper synchronous fetchers used with asyncio.to_thread
def fetch_coingecko_price() -> float:
    url = "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd"
    req = urllib.request.Request(url, headers={"User-Agent": "btc-price-agent/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = resp.read()
            obj = json.loads(data.decode("utf-8"))
            price = obj.get("bitcoin", {}).get("usd")
            if price is None:
                raise Exception("Unexpected CoinGecko response format")
            return float(price)
    except urllib.error.HTTPError as e:
        raise Exception(f"CoinGecko HTTP error: {e.code}")
    except Exception:
        raise


def fetch_coinbase_price() -> float:
    url = "https://api.coinbase.com/v2/prices/BTC-USD/spot"
    req = urllib.request.Request(url, headers={"User-Agent": "btc-price-agent/0.1", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = resp.read()
            obj = json.loads(data.decode("utf-8"))
            amount = obj.get("data", {}).get("amount")
            if amount is None:
                raise Exception("Unexpected Coinbase response format")
            return float(amount)
    except urllib.error.HTTPError as e:
        raise Exception(f"Coinbase HTTP error: {e.code}")
    except Exception:
        raise


# FastAPI application
app = FastAPI()
agent = BtcPriceAgent()
_start_time = time.time()


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@app.on_event("startup")
async def startup_event():
    port = int(os.environ.get("PORT", "8000"))
    print("STATUS:starting:Initializing BTC Price Agent")
    print(f"STATUS:running:Server started on port {port}")
    print("STATUS:waiting:Idle, waiting for requests")


@app.on_event("shutdown")
async def shutdown_event():
    print("STATUS:stopping:Shutting down")
    print("STATUS:stopped:Agent terminated")


@app.get("/.well-known/agent.json")
async def agent_card():
    port = int(os.environ.get("PORT", "8000"))
    card = {
        "name": agent.AGENT_NAME,
        "description": agent.DESCRIPTION,
        "version": "0.1.0",
        "protocol": "a2a",
        "capabilities": agent.CAPABILITIES,
        "endpoint": f"http://localhost:{port}",
    }
    return JSONResponse(content=card)


@app.get("/health")
async def health():
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
async def handle(req: Request):
    try:
        body = await req.json()
    except Exception:
        body = {}
    req_str = body.get("request", "") if isinstance(body, dict) else ""
    ctx = body.get("context", {}) if isinstance(body, dict) else {}

    print("STATUS:executing:Fetching current BTC price in USD")
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
        print("STATUS:failed:Error retrieving BTC price")
        err: dict[str, Any] = {
            "success": False,
            "response": None,
            "error": "Failed to retrieve BTC price from market data provider",
            "metadata": {},
        }
        return JSONResponse(content=err, status_code=500)
