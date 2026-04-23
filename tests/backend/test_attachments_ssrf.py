"""Security tests for URL-based attachment fetching."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from app.backend.attachments import AttachmentConfig, AttachmentManager


@dataclass
class _FakeResponse:
    url: str
    status: int = 200
    body: bytes = b"hello"
    headers: dict[str, str] = field(default_factory=dict)

    async def __aenter__(self) -> _FakeResponse:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        del exc_type, exc, tb
        return None

    async def read(self) -> bytes:
        return self.body

    def raise_for_status(self) -> None:
        if self.status >= 400:
            raise RuntimeError(f"HTTP {self.status}")


class _FakeClientSession:
    def __init__(self, responses: list[_FakeResponse] | None = None, connector: Any = None) -> None:
        self._responses = list(responses or [])
        self.connector = connector

    async def __aenter__(self) -> _FakeClientSession:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        del exc_type, exc, tb
        return None

    def get(self, url: str, *, allow_redirects: bool = False) -> _FakeResponse:
        assert self.connector is not None
        assert allow_redirects is False
        if self._responses:
            response = self._responses.pop(0)
            response.url = url
            return response
        return _FakeResponse(url=url)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/internal.txt",
        "http://169.254.169.254/latest/meta-data",
        "http://10.0.0.1/secret.txt",
        "http://[::1]/internal.txt",
        "file:///etc/passwd",
        "ftp://example.com/archive.zip",
    ],
)
async def test_process_url_rejects_private_and_unsupported_targets(
    url: str,
):
    manager = AttachmentManager(AttachmentConfig(max_file_size_mb=1))

    result = await manager.process_url(url)

    assert result.error is not None
    if url.startswith(("file://", "ftp://")):
        assert "Only http:// and https://" in result.error
    else:
        assert "blocked address" in result.error


@pytest.mark.asyncio
async def test_process_url_rejects_oversized_content_length(monkeypatch: pytest.MonkeyPatch):
    import aiohttp

    manager = AttachmentManager(AttachmentConfig(max_file_size_mb=1))

    async def _resolve(_parsed_url) -> list[str]:
        return ["93.184.216.34"]

    monkeypatch.setattr(manager, "_resolve_url_addresses", _resolve)
    monkeypatch.setattr(
        aiohttp,
        "ClientSession",
        lambda timeout=None, connector=None: _FakeClientSession(
            [
                _FakeResponse(
                    url="http://example.com/file.txt",
                    headers={"content-length": str(2 * 1024 * 1024)},
                )
            ],
            connector=connector,
        ),
    )

    result = await manager.process_url("http://example.com/file.txt")

    assert result.error is not None
    assert "Remote file too large" in result.error


@pytest.mark.asyncio
async def test_process_url_rejects_redirects_to_private_ips(monkeypatch: pytest.MonkeyPatch):
    import aiohttp

    manager = AttachmentManager(AttachmentConfig(max_file_size_mb=1))

    async def _resolve(parsed_url) -> list[str]:
        if parsed_url.hostname == "example.com":
            return ["93.184.216.34"]
        return ["127.0.0.1"]

    monkeypatch.setattr(manager, "_resolve_url_addresses", _resolve)
    monkeypatch.setattr(
        aiohttp,
        "ClientSession",
        lambda timeout=None, connector=None: _FakeClientSession(
            [
                _FakeResponse(
                    url="http://example.com/file.txt",
                    status=302,
                    headers={"location": "http://127.0.0.1/secret.txt"},
                )
            ],
            connector=connector,
        ),
    )

    result = await manager.process_url("http://example.com/file.txt")

    assert result.error is not None
    assert "blocked address" in result.error


@pytest.mark.asyncio
async def test_process_url_uses_pinned_resolver_addresses(monkeypatch: pytest.MonkeyPatch):
    import aiohttp

    manager = AttachmentManager(AttachmentConfig(max_file_size_mb=1))
    captured: dict[str, Any] = {}

    async def _resolve(_parsed_url) -> list[str]:
        return ["93.184.216.34", "93.184.216.35"]

    monkeypatch.setattr(manager, "_resolve_url_addresses", _resolve)

    def _make_session(timeout=None, connector=None):
        captured["connector"] = connector
        return _FakeClientSession(
            [_FakeResponse(url="https://example.com/file.txt")], connector=connector
        )

    monkeypatch.setattr(aiohttp, "ClientSession", _make_session)

    result = await manager.process_url("https://example.com/file.txt")

    assert result.error is None
    resolver = captured["connector"]._resolver
    resolved = await resolver.resolve("example.com", 443)
    assert [entry["host"] for entry in resolved] == ["93.184.216.34", "93.184.216.35"]
