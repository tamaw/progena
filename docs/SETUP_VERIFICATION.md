# Setup & Verification Guide: Progenitor ↔ Builder Integration

## Prerequisites

✅ All prerequisites already met:
- Python 3.12+ (with virtual environment activated)
- `httpx` library (for HTTP requests)
- `pydantic` v2 (for data validation)
- `uvicorn` (for running agents)
- GitHub Copilot CLI installed (`copilot`)
- GitHub CLI installed (`gh`)
- GitHub authenticated (`gh auth login`)

## Step 1: Verify File Setup

Check that all new files exist:

```bash
# Agent configuration
ls -la data/config/agents.json  # Should exist

# Tool implementation
ls -la app/progenitor/tools/invoke_builder.py  # Should exist

# Demo script
ls -la scripts/demo_builder.py  # Should exist

# Documentation
ls -la docs/builder_INTEGRATION.md  # Should exist
ls -la docs/IMPLEMENTATION_SUMMARY.md  # Should exist

# Tests
ls -la tests/progenitor/tools/test_invoke_builder.py  # Should exist
```

All should show **file exists**.

## Step 2: Start Builder

In Terminal 1:
```bash
cd d:\progenitor
python -m uvicorn app.agents.builder_progenitor.agent:app --port 8108 --reload
```

Check for output:
```
INFO:     Started server process [PID]
INFO:     Uvicorn running on http://0.0.0.0:8108
```

## Step 3: Verify Health Endpoint

In Terminal 2:
```bash
curl http://localhost:8108/health
```

Expected output:
```json
{
  "status": "healthy",
  "name": "Builder",
  "version": "0.1.0",
  "uptime_seconds": 42.5
}
```

## Step 4: Test Direct A2A Invocation

Run the demo script:
```bash
cd d:\progenitor
python scripts/demo_builder.py
```

Expected output:
```
======================================================================
  DEMO: Invoke Builder from Progenitor (A2A Protocol)
======================================================================

[STEP 1] Checking if Builder is running...
----------------------------------------------------------------------
[OK] Builder is running
     Status: healthy
     Version: 0.1.0
     Uptime: 42.5s

[STEP 2] Invoking Builder asynchronously...
----------------------------------------------------------------------
[OK] Task accepted!
     Task ID: copilot_abc123def456...

[STEP 3] A2A Response:
----------------------------------------------------------------------
{
  "timeout": "expected",
  "task": "copilot_abc123def456"
}

[SUMMARY]
----------------------------------------------------------------------
Task ID         : copilot_abc123def456
Status          : ACCEPTED (processing asynchronously)
Expected output : Pull request on GitHub
```

## Step 5: Verify Tool Auto-Discovery

In Python REPL:
```python
from app.progenitor.nodes.node_helpers.registry import get_system_tool

# Check if tool is discovered
tool = get_system_tool("invoke_builder")
print(f"Tool found: {tool is not None}")
print(f"Tool name: {tool.name if tool else 'NOT FOUND'}")
print(f"Tool description: {tool.description if tool else 'N/A'}")
```

Expected:
```
Tool found: True
Tool name: invoke_builder
Tool description: Delegate a code generation task to Builder...
```

## Step 6: Verify Agent Configuration Loading

```python
from pathlib import Path
import json

config_path = Path("data/config/agents.json")
if config_path.exists():
    with open(config_path) as f:
        config = json.load(f)

    if "builder" in config:
        print("✓ Builder registered in agents.json")
        print(f"  - URL: {config['builder'].get('url')}")
        print(f"  - Capabilities: {config['builder'].get('capabilities')}")
    else:
        print("✗ Builder NOT in agents.json")
else:
    print("✗ agents.json does not exist")
```

Expected:
```
✓ Builder registered in agents.json
  - URL: http://localhost:8108
  - Capabilities: ['build', 'plan', 'test', 'lint', 'pr', 'async_tasks', ...]
```

## Step 7: Run Unit Tests

```bash
cd d:\progenitor

# Run all tests
pytest tests/progenitor/tools/test_invoke_builder.py -v

# Or specific test
pytest tests/progenitor/tools/test_invoke_builder.py::TestInvokeBuilderTool::test_tool_properties -v
```

Expected:
```
tests/progenitor/tools/test_invoke_builder.py::TestInvokeBuilderTool::test_tool_properties PASSED
tests/progenitor/tools/test_invoke_builder.py::TestInvokeBuilderTool::test_auto_discovery PASSED
...
```

## Step 8: Test Progenitor Integration (Manual)

Start Progenitor:
```bash
cd d:\progenitor
python -m app.progenitor.main
```

Then interact with it:
```
User: "What tools are available?"
→ Should list all system tools including invoke_builder

User: "Can you help me create a README for Builder?"
→ Should recognize this as a build task
→ Should invoke invoke_builder tool
→ Should return task_id

User: "Create a comprehensive README for Builder agent documenting its purpose, capabilities, and usage."
→ Should trigger Builder to:
   1. Create a git worktree
   2. Generate README with Copilot
   3. Test the changes
   4. Lint the code
   5. Create a pull request
```

## Verification Checklist

Run these in order:

```bash
# 1. Verify files exist
✓ data/config/agents.json
✓ app/progenitor/tools/invoke_builder.py
✓ scripts/demo_builder.py
✓ docs/builder_INTEGRATION.md
✓ tests/progenitor/tools/test_invoke_builder.py

# 2. Start Builder (Terminal 1)
python -m uvicorn app.agents.builder_progenitor.agent:app --port 8108
# Wait for: "Uvicorn running on http://0.0.0.0:8108"

# 3. Test health (Terminal 2)
curl http://localhost:8108/health
# Expect: {"status": "healthy", ...}

# 4. Run demo (Terminal 2)
python scripts/demo_builder.py
# Expect: [OK] Task accepted!

# 5. Run unit tests (Terminal 2)
pytest tests/progenitor/tools/test_invoke_builder.py -v
# Expect: All tests PASSED

# 6. Check agent config (Terminal 2)
python -c "import json; print(json.loads(open('data/config/agents.json').read()))"
# Expect: builder with url and capabilities

# 7. Check tool discovery (Terminal 2)
python -c "from app.progenitor.nodes.node_helpers.registry import get_system_tool; t = get_system_tool('invoke_builder'); print(f'Found: {t is not None}')"
# Expect: Found: True

# 8. Manual Progenitor test (Terminal 3)
python -m app.progenitor.main
# Ask: "Create a README for Builder"
# Expect: Task accepted with task_id
```

## Troubleshooting

### Builder won't start

**Error**: `Address already in use`
```bash
# Kill existing process
lsof -i :8108
kill -9 <PID>
```

**Error**: `ModuleNotFoundError: No module named 'app.agents.builder_progenitor'`
```bash
# Ensure venv is activated and working directory is correct
. .venv/Scripts/activate  # or: & .\.venv\Scripts\Activate.ps1
cd d:\progenitor
```

### Demo script fails

**Error**: `httpx.ConnectError: Cannot connect to Builder`
```bash
# Make sure Builder is running in another terminal
python -m uvicorn app.agents.builder_progenitor.agent:app --port 8108
```

### Tool not auto-discovered

**Error**: `Tool 'invoke_builder' not found`
```bash
# Restart Python interpreter for discovery
python
>>> from app.progenitor.nodes.node_helpers.registry import _SYSTEM_TOOLS
>>> _SYSTEM_TOOLS.clear()  # Force reload
```

### Tests fail

```bash
# Make sure Builder is running
python -m uvicorn app.agents.builder_progenitor.agent:app --port 8108

# Then run tests
pytest tests/progenitor/tools/test_invoke_builder.py -v -s
```

## Performance Expectations

| Operation | Time | Notes |
|-----------|------|-------|
| Health check | ~100ms | Very fast |
| Demo script execution | 30s | Times out waiting for task (expected) |
| Unit test run | ~5-10s | Some tests connect to Builder |
| PR creation | 3-8 min | Full workflow (plan, build, test, lint, pr) |

## Next Steps

### After Verification

1. **Use Progenitor normally** - invoke_builder is now available
2. **Monitor Builder logs** - see what's happening behind the scenes
3. **Check GitHub PRs** - see the created pull requests
4. **Extend with more agents** - add Observer Agent, etc.

### Monitor Execution

```bash
# Watch Builder logs
tail -f logs/builder/builder.stdout.log

# Check worktrees created
ls -la worktrees/

# Check git branches
git branch -a | grep progenitor/

# Monitor GitHub PRs
open https://github.com/tamaw/progenitor/pulls
```

## Success Criteria

All of these should be true:

- [x] Builder runs on port 8108
- [x] Health endpoint returns 200 OK
- [x] A2A `message/send` requests are accepted
- [x] Demo script executes and returns task_id
- [x] Unit tests run without errors
- [x] invoke_builder tool is auto-discovered
- [x] Configuration loads from agents.json
- [x] Progenitor can invoke tool when appropriate
- [x] Documentation is complete
- [x] No changes needed to existing code (other than config/tools)

## Support

If anything doesn't work:

1. **Check logs**:
   ```bash
   tail -f logs/builder/builder.stdout.log
   tail -f logs/progenitor/progenitor.log
   ```

2. **Review documentation**:
   - `docs/builder_INTEGRATION.md`
   - `docs/IMPLEMENTATION_SUMMARY.md`

3. **Run demo script**:
   ```bash
   python scripts/demo_builder.py
   ```

4. **Check GitHub issues** (if applicable)

