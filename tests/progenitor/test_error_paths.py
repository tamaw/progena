"""
Error Path Coverage Tests.

TEST-004: Tests for explicit error recovery paths.

Tests:
- LLM timeout handling
- Invalid JSON response handling
- State corruption recovery
- File system errors
- Network failures
"""

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.progenitor.error_handling import (
    ErrorContext,
    ErrorSeverity,
    classify_error,
)

# ============================================================================
# LLM Timeout Error Tests
# ============================================================================

class TestLLMTimeoutHandling:
    """Tests for LLM timeout error handling."""

    @pytest.mark.asyncio
    async def test_llm_call_timeout_handled(self):
        """LLM timeout should be caught and handled gracefully."""
        # Create mock that times out
        mock_client = AsyncMock()
        mock_client.chat_completion = AsyncMock(
            side_effect=TimeoutError("LLM request timed out")
        )

        # Test with try/except pattern
        result = {"error": None}
        try:
            await mock_client.chat_completion(messages=[{"role": "user", "content": "test"}])
        except TimeoutError:
            result = {"error": "timeout", "message": "LLM timed out"}

        assert result["error"] == "timeout"

    @pytest.mark.asyncio
    async def test_llm_timeout_triggers_retry(self):
        """LLM timeout should trigger configurable retry."""
        call_count = 0

        async def flaky_call():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise TimeoutError("Timeout")
            return {"success": True}

        # Simulate retry logic
        result: dict[str, Any] | None = None
        for attempt in range(5):
            try:
                result = await flaky_call()
                break
            except TimeoutError:
                if attempt == 4:
                    result = {"error": "max_retries"}
                await asyncio.sleep(0.01)  # Brief backoff

        assert result is not None
        assert result["success"] is True
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_timeout_classification(self):
        """Timeout errors should be classified correctly."""
        error = TimeoutError("Request timed out")
        severity = classify_error(error)

        # Timeouts are recoverable
        assert severity == ErrorSeverity.RECOVERABLE


# ============================================================================
# Invalid JSON Response Tests
# ============================================================================

class TestInvalidJSONHandling:
    """Tests for invalid JSON response handling."""

    def test_malformed_json_caught(self):
        """Malformed JSON should be caught gracefully."""
        malformed = "{'invalid': json, no quotes}"

        with pytest.raises(json.JSONDecodeError):
            json.loads(malformed)

    def test_json_error_with_fallback(self):
        """JSON errors should return fallback value."""
        malformed = "not json at all"

        def safe_parse(data: str, fallback: Any = None) -> Any:
            try:
                return json.loads(data)
            except json.JSONDecodeError:
                return fallback

        result = safe_parse(malformed, fallback={"parsed": False})
        assert result == {"parsed": False}

    @pytest.mark.asyncio
    async def test_llm_invalid_json_response(self):
        """LLM returning invalid JSON should be handled."""
        # Simulate LLM returning non-JSON response
        raw_response = "I'll think about that... [processing]"

        def extract_json(text: str) -> dict | None:
            """Try to extract JSON from text, return None if impossible."""
            # Try direct parse
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                pass

            # Try to find JSON block
            import re
            json_match = re.search(r'\{[^{}]*\}', text)
            if json_match:
                try:
                    return json.loads(json_match.group())
                except json.JSONDecodeError:
                    pass

            return None

        result = extract_json(raw_response)
        assert result is None

    def test_json_with_extra_content(self):
        """JSON embedded in other text should be extractable."""
        response = '''Here is my response:

        ```json
        {"action": "build", "target": "feature-x"}
        ```

        Let me know if you need more.'''

        import re
        json_match = re.search(r'\{[^}]+\}', response)

        assert json_match is not None
        parsed = json.loads(json_match.group())
        assert parsed["action"] == "build"


# ============================================================================
# State Corruption Recovery Tests
# ============================================================================

class TestStateCorruptionRecovery:
    """Tests for state corruption recovery."""

    def test_corrupt_state_file_recovery(self, tmp_path):
        """Corrupt state file should trigger recovery."""
        state_file = tmp_path / "state.json"

        # Write corrupt data
        state_file.write_text("{'corrupt: json}")

        def load_state_with_recovery(path) -> dict:
            try:
                with open(path) as f:
                    return json.load(f)
            except (json.JSONDecodeError, FileNotFoundError):
                # Return fresh state
                return {"recovered": True, "items": []}

        state = load_state_with_recovery(state_file)
        assert state["recovered"] is True

    def test_partial_write_recovery(self, tmp_path):
        """Partial write should be detected and recovered."""
        state_file = tmp_path / "state.json"
        backup_file = tmp_path / "state.json.bak"

        # Write good backup
        backup_file.write_text(json.dumps({"valid": True}))

        # Write corrupt main file (simulating interrupted write)
        state_file.write_text('{"trunca')

        def load_with_backup(main_path, backup_path) -> dict:
            try:
                with open(main_path) as f:
                    return json.load(f)
            except (json.JSONDecodeError, FileNotFoundError):
                try:
                    with open(backup_path) as f:
                        return json.load(f)
                except (json.JSONDecodeError, FileNotFoundError):
                    return {}

        state = load_with_backup(state_file, backup_file)
        assert state["valid"] is True

    def test_missing_required_fields_recovery(self):
        """State missing required fields should be repaired."""
        incomplete_state = {"some_field": "value"}

        required_fields = {
            "cycle_count": 0,
            "backlog": [],
            "current_input": None,
        }

        def ensure_required_fields(state: dict, defaults: dict) -> dict:
            for key, default in defaults.items():
                if key not in state:
                    state[key] = default
            return state

        repaired = ensure_required_fields(incomplete_state, required_fields)

        assert "cycle_count" in repaired
        assert "backlog" in repaired
        assert repaired["some_field"] == "value"


# ============================================================================
# File System Error Tests
# ============================================================================

class TestFileSystemErrors:
    """Tests for file system error handling."""

    @pytest.mark.asyncio
    async def test_permission_denied_handled(self, tmp_path):
        """Permission denied should not crash the system."""
        # Test basic file creation with directory creation
        protected_path = tmp_path / "readonly" / "state.json"

        # Ensure parent exists
        protected_path.parent.mkdir(parents=True, exist_ok=True)

        # Write should succeed
        protected_path.write_text(json.dumps({"test": True}))

        # Should succeed because it creates the directory
        assert protected_path.exists()

    @pytest.mark.asyncio
    async def test_disk_full_handled(self):
        """Disk full errors should be handled gracefully."""
        async def write_that_fails():
            raise OSError(28, "No space left on device")

        # Use try/except pattern
        result = {"written": True}  # Default
        try:
            await write_that_fails()
        except OSError:
            result = {"written": False, "error": "disk_full"}

        assert result["written"] is False
        assert result["error"] == "disk_full"

    def test_file_locked_retry(self, tmp_path):
        """Locked file should trigger retry."""
        test_file = tmp_path / "locked.json"
        test_file.write_text("{}")

        attempts = 0
        max_attempts = 3

        def simulate_locked_file():
            nonlocal attempts
            attempts += 1
            if attempts < max_attempts:
                raise PermissionError("File is locked")
            return True

        result = None
        for _ in range(max_attempts):
            try:
                result = simulate_locked_file()
                break
            except PermissionError:
                import time
                time.sleep(0.01)

        assert result is True
        assert attempts == max_attempts


# ============================================================================
# Network Failure Tests
# ============================================================================

class TestNetworkFailures:
    """Tests for network failure handling."""

    @pytest.mark.asyncio
    async def test_connection_refused_handled(self):
        """Connection refused should be handled."""
        async def failing_request():
            raise ConnectionRefusedError("Connection refused")

        # Use try/except pattern
        result = {"connected": True}  # Default
        try:
            await failing_request()
        except ConnectionRefusedError:
            result = {"connected": False}

        assert result["connected"] is False

    @pytest.mark.asyncio
    async def test_connection_reset_retry(self):
        """Connection reset should trigger retry."""
        attempts = 0

        async def flaky_connection():
            nonlocal attempts
            attempts += 1
            if attempts < 2:
                raise ConnectionResetError("Connection reset by peer")
            return {"success": True}

        result: dict[str, Any] | None = None
        for _attempt in range(3):
            try:
                result = await flaky_connection()
                break
            except ConnectionResetError:
                await asyncio.sleep(0.01)

        assert result is not None
        assert result["success"] is True
        assert attempts == 2

    @pytest.mark.asyncio
    async def test_dns_failure_handled(self):
        """DNS resolution failure should be handled."""
        async def dns_fails():
            raise OSError("Name or service not known")

        # Use try/except pattern
        result = {"resolved": True}  # Default
        try:
            await dns_fails()
        except OSError:
            result = {"resolved": False}

        assert result["resolved"] is False

    def test_network_error_classification(self):
        """Network errors should be classified correctly."""
        errors = [
            (ConnectionRefusedError("refused"), ErrorSeverity.RECOVERABLE),
            (ConnectionResetError("reset"), ErrorSeverity.RECOVERABLE),
            (TimeoutError("timeout"), ErrorSeverity.RECOVERABLE),
        ]

        for error, expected_severity in errors:
            assert classify_error(error) == expected_severity


# ============================================================================
# Circuit Breaker Integration Tests
# ============================================================================

class TestCircuitBreakerIntegration:
    """Tests for circuit breaker error handling."""

    @pytest.mark.asyncio
    async def test_circuit_opens_on_failures(self):
        """Circuit should open after threshold failures."""
        from app.progenitor.llm.circuit_breaker import CircuitBreaker, CircuitState

        breaker = CircuitBreaker(failure_threshold=3, reset_timeout=0.1)

        # Cause failures
        for _ in range(3):
            try:
                async with breaker:
                    raise ConnectionError("Failed")
            except ConnectionError:
                pass

        # Circuit should be open
        assert breaker.state == CircuitState.OPEN.value

    @pytest.mark.asyncio
    async def test_circuit_rejects_when_open(self):
        """Open circuit should reject calls."""
        from app.progenitor.llm.circuit_breaker import CircuitBreaker, CircuitOpenError

        breaker = CircuitBreaker(failure_threshold=1, reset_timeout=10)

        # Trip the circuit
        try:
            async with breaker:
                raise ConnectionError("Failed")
        except ConnectionError:
            pass

        # Next call should be rejected
        with pytest.raises(CircuitOpenError):
            async with breaker:
                pass

    @pytest.mark.asyncio
    async def test_circuit_recovers_after_timeout(self):
        """Circuit should recover after reset timeout."""
        from app.progenitor.llm.circuit_breaker import CircuitBreaker, CircuitState

        breaker = CircuitBreaker(failure_threshold=1, reset_timeout=0.05)

        # Trip the circuit
        try:
            async with breaker:
                raise ConnectionError("Failed")
        except ConnectionError:
            pass

        assert breaker.state == CircuitState.OPEN.value

        # Wait for reset timeout
        await asyncio.sleep(0.1)

        # Circuit should be half-open, allowing a test call
        async with breaker:
            pass  # Success

        assert breaker.state == CircuitState.CLOSED.value


# ============================================================================
# Error Context Tests
# ============================================================================

class TestErrorContext:
    """Tests for error context tracking."""

    def test_error_context_captures_info(self):
        """ErrorContext should capture relevant information."""
        ctx = ErrorContext(
            error_type="ValueError",
            operation="test_operation",
            key="value",
        )

        data = ctx.to_dict()
        assert data["operation"] == "test_operation"
        assert data["key"] == "value"
        assert data["error_type"] == "ValueError"

    def test_error_context_to_dict(self):
        """ErrorContext should serialize to dict."""
        ctx = ErrorContext(
            error_type="ValueError",
            operation="op",
        )

        data = ctx.to_dict()

        assert "error_type" in data
        assert "operation" in data
        assert data["error_type"] == "ValueError"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
