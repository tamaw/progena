# Progenitor Integration Tests

Live integration tests that interact with the Progenitor agent via IPC.

## Overview

These tests communicate with a running Progenitor instance through the IPC protocol.
Results are written to files for manual review instead of automated pass/fail assertions.

## Prerequisites

**Progenitor must be running before tests can execute.**

Start Progenitor:
```bash
# Option 1: Start all services (recommended)
# Use VS Code Task: "Start All (dev)"

# Option 2: Start Progenitor + backend manually
python -m app.progenitor.main
python -m uvicorn app.backend.main:app --host 0.0.0.0 --port 8000

# Option 3: Use Docker stack
docker compose -f docker/docker-compose.progenitor.yml up --build -d
```

Verify Progenitor is running:
```bash
# Check if IPC port is open
python tests/integration/helpers.py
```

## Test Coverage

- **Chat**: Basic user message and response
- **Chat Streaming**: Streaming response
- **Schedule**: Scheduled task creation
- **Build**: Agent building workflow discussion
- **System Tool**: System tool invocation
- **MCP Tool**: MCP tool interaction
- **Multi-turn**: Conversation state persistence

## Running Tests

```bash
# Activate venv
& .\.venv\Scripts\Activate.ps1  # Windows
source .venv/bin/activate        # Linux/Mac

# Run all integration tests
pytest tests/integration/ -v -m integration

# Run specific test
pytest tests/integration/test_live_ipc.py::test_chat_basic -v

# Run tests and skip if Progenitor not running
pytest tests/integration/ -v --skip-integration

# Run tests directly (without pytest)
python tests/integration/test_live_ipc.py
```

## Output

Test results are written to `tests/integration/results/` as JSON files for manual inspection.

Each result file contains:
- Test name and timestamp
- Request message sent to Progenitor
- Response received (or error)
- Duration
- Success status

## Example Result

```json
{
  "test_name": "chat_basic",
  "timestamp": "2026-02-11T15:30:00",
  "request": "What is 2 + 2?",
  "response": "2 + 2 equals 4.",
  "success": true,
  "duration_seconds": 1.23
}
```

## Viewing Results

```bash
# View summary
python tests/integration/helpers.py

# View specific result
cat tests/integration/results/chat_basic_*.json
```

## Cleanup

After running tests, stop Progenitor to free resources:

```bash
# Find and stop Progenitor process
Get-Process python* | Where-Object { (Get-NetTCPConnection -OwningProcess $_.Id -ErrorAction SilentlyContinue).LocalPort -eq 9500 } | Stop-Process -Force

# Or stop all test services
taskkill /F /IM python.exe /FI "WINDOWTITLE eq Progenitor*"
```

## Notes

- Tests use deterministic messages when possible
- Results are timestamped for tracking across runs
- Failed tests include error details in the result file
- The `results/` directory is gitignored
- Always stop Progenitor after testing to avoid resource leaks

