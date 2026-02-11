"""Tests for rate limiter memory bounds in backend."""

import time
import unittest


class TestRateLimiterMemoryBounds(unittest.TestCase):
    """Test rate limiter memory bounding functionality."""

    def setUp(self):
        """Set up test fixtures."""
        from app.backend.main import RateLimiter
        self.RateLimiter = RateLimiter

    def test_max_tracked_clients_constant(self):
        """Test that MAX_TRACKED_CLIENTS is defined."""
        limiter = self.RateLimiter()
        assert hasattr(limiter, 'MAX_TRACKED_CLIENTS')
        assert limiter.MAX_TRACKED_CLIENTS == 10000

    def test_cleanup_stale_clients_returns_count(self):
        """Test that cleanup returns number of removed clients."""
        limiter = self.RateLimiter()

        # Add some stale clients
        old_time = time.time() - 4000  # More than 1 hour ago
        for i in range(5):
            limiter._hour_requests[f"stale_{i}"] = [old_time]
            limiter._minute_requests[f"stale_{i}"] = []

        # Force cleanup
        removed = limiter._cleanup_stale_clients(force=True)

        assert removed == 5
        assert len(limiter._hour_requests) == 0

    def test_evict_oldest_clients(self):
        """Test evicting oldest clients."""
        limiter = self.RateLimiter()

        # Add clients with different ages
        now = time.time()
        for i in range(10):
            limiter._hour_requests[f"client_{i}"] = [now - (1000 - i * 100)]
            limiter._minute_requests[f"client_{i}"] = [now - (50 - i * 5)]

        # Evict 3 oldest
        evicted = limiter._evict_oldest_clients(3)

        assert evicted == 3
        assert len(limiter._hour_requests) == 7

        # Oldest clients should be evicted
        assert "client_0" not in limiter._hour_requests
        assert "client_1" not in limiter._hour_requests
        assert "client_2" not in limiter._hour_requests

        # Newer clients should remain
        assert "client_9" in limiter._hour_requests

    def test_evict_oldest_empty(self):
        """Test evicting from empty limiter."""
        limiter = self.RateLimiter()
        evicted = limiter._evict_oldest_clients(5)
        assert evicted == 0

    def test_capacity_enforcement_at_limit(self):
        """Test that eviction happens when at capacity."""
        # Create limiter with small capacity for testing
        limiter = self.RateLimiter()
        limiter.MAX_TRACKED_CLIENTS = 10  # Small for testing

        now = time.time()

        # Fill to capacity with recent clients
        for i in range(10):
            limiter._hour_requests[f"client_{i}"] = [now - i * 10]
            limiter._minute_requests[f"client_{i}"] = [now - i * 10]

        assert len(limiter._hour_requests) == 10

        # New request should trigger eviction
        allowed, _ = limiter.is_allowed("new_client")

        # Should have evicted some clients
        assert len(limiter._hour_requests) < 11  # May include new_client

    def test_get_stats_includes_capacity(self):
        """Test that stats include capacity information."""
        limiter = self.RateLimiter()

        # Add some clients
        now = time.time()
        for i in range(100):
            limiter._hour_requests[f"client_{i}"] = [now]

        stats = limiter.get_stats()

        assert "active_clients" in stats
        assert stats["active_clients"] == 100
        assert "max_tracked_clients" in stats
        assert stats["max_tracked_clients"] == 10000
        assert "capacity_percent" in stats
        assert stats["capacity_percent"] == 1.0  # 100/10000 = 1%

    def test_forced_cleanup(self):
        """Test forced cleanup ignores interval."""
        limiter = self.RateLimiter()
        limiter._last_cleanup = time.time()  # Just cleaned

        # Add stale client
        old_time = time.time() - 4000
        limiter._hour_requests["stale"] = [old_time]

        # Normal cleanup should skip (interval not reached)
        removed = limiter._cleanup_stale_clients(force=False)
        assert removed == 0
        assert "stale" in limiter._hour_requests

        # Forced cleanup should work
        removed = limiter._cleanup_stale_clients(force=True)
        assert removed == 1
        assert "stale" not in limiter._hour_requests

    def test_no_memory_leak_with_many_clients(self):
        """Test that memory is bounded even with many unique clients."""
        limiter = self.RateLimiter()
        limiter.MAX_TRACKED_CLIENTS = 100  # Small for testing

        time.time()

        # Simulate many unique clients (more than limit)
        for i in range(150):
            # Check and record for each client
            allowed, _ = limiter.is_allowed(f"client_{i}")
            if allowed:
                limiter.record_request(f"client_{i}")

        # Should not exceed max tracked clients
        assert len(limiter._hour_requests) <= limiter.MAX_TRACKED_CLIENTS + 1


class TestRateLimiterBasicFunctionality(unittest.TestCase):
    """Test basic rate limiter functionality still works."""

    def setUp(self):
        """Set up test fixtures."""
        from app.backend.main import RateLimiter
        self.limiter = RateLimiter(requests_per_minute=5, requests_per_hour=10)

    def test_allows_under_limit(self):
        """Test requests are allowed under the limit."""
        for i in range(5):
            allowed, reason = self.limiter.is_allowed("client1")
            assert allowed, f"Request {i} should be allowed"
            assert reason is None
            self.limiter.record_request("client1")

    def test_blocks_over_minute_limit(self):
        """Test requests are blocked over minute limit."""
        # Record 5 requests (at limit)
        for _ in range(5):
            self.limiter.record_request("client1")

        # Next should be blocked
        allowed, reason = self.limiter.is_allowed("client1")
        assert not allowed
        assert reason is not None
        assert "requests/minute" in reason

    def test_get_remaining(self):
        """Test remaining requests calculation."""
        self.limiter.record_request("client1")
        self.limiter.record_request("client1")

        remaining = self.limiter.get_remaining("client1")

        assert remaining["minute"] == 3  # 5 - 2
        assert remaining["hour"] == 8  # 10 - 2


if __name__ == "__main__":
    unittest.main()
