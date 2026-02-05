# BTC Price Agent

Purpose
-------

Returns the current Bitcoin (BTC) price in USD using live market data.

API Data Sources
----------------

Primary: CoinGecko
- https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd

Fallback: CoinDesk
- https://api.coindesk.com/v1/bpi/currentprice/BTC.json

Example curl request:

curl -X POST http://localhost:8000/ -H "Content-Type: application/json" -d '{"request": "price", "id": "req-123"}'

Example successful response:

{
  "success": true,
  "response": {
    "asset": "BTC",
    "currency": "USD",
    "price": 43210.55,
    "source": "CoinGecko",
    "timestamp": "2024-01-01T00:00:00Z"
  },
  "error": null,
  "metadata": {
    "request_id": "req-123"
  }
}

Example failure response:

{
  "success": false,
  "response": null,
  "error": "Failed to retrieve BTC price from market data provider",
  "metadata": {
    "request_id": "req-123"
  }
}

How to run the agent:

python agent.py --port 8000
