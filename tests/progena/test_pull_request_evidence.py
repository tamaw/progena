"""Read-only GitHub pull request lookup used as review provenance evidence."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import aiohttp
import pytest

from src.progena.repositories.pull_requests import (
    PullRequestLookupError,
    fetch_pull_request_evidence,
)

PR = "https://github.com/tamaw/progena/pull/415"
API = "https://api.github.com/repos/tamaw/progena/pulls/415"
SHA = "a" * 40
NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)


def _payload(**changes: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "number": 415,
        "html_url": PR,
        "state": "open",
        "head": {"sha": SHA, "ref": "agent-approval-handoff-budget"},
    }
    payload.update(changes)
    return payload


class FakeApi:
    def __init__(self, status: int = 200, payload: Any = None, error: Exception | None = None):
        self.status = status
        self.payload = _payload() if payload is None else payload
        self.error = error
        self.calls: list[str] = []

    async def __call__(self, url: str) -> tuple[int, Any]:
        self.calls.append(url)
        if self.error is not None:
            raise self.error
        return self.status, self.payload


async def fetch(url: str = PR, api: FakeApi | None = None) -> dict[str, Any]:
    return await fetch_pull_request_evidence(url, get_json=api or FakeApi(), now=lambda: NOW)


class TestResponseParsing:
    @pytest.mark.asyncio
    async def test_successful_fetch_is_verified_evidence(self):
        api = FakeApi()
        assert await fetch(api=api) == {
            "url": PR,
            "head_sha": SHA,
            "verified": True,
            "observed_at": "2026-10-23T12:00:00+00:00",
        }
        assert api.calls == [API]

    @pytest.mark.asyncio
    async def test_url_and_sha_are_canonicalized(self):
        api = FakeApi(
            payload=_payload(
                html_url="https://github.com/TamaW/Progena/pull/415", head={"sha": SHA.upper()}
            )
        )
        evidence = await fetch("https://github.com/TamaW/Progena/pull/415/", api)
        assert evidence["url"] == PR
        assert evidence["head_sha"] == SHA
        assert evidence["verified"] is True
        assert api.calls == [API]

    @pytest.mark.asyncio
    async def test_sha256_heads_are_accepted(self):
        evidence = await fetch(api=FakeApi(payload=_payload(head={"sha": "b" * 64})))
        assert evidence["head_sha"] == "b" * 64

    @pytest.mark.asyncio
    async def test_observed_at_is_stamped_by_the_reader(self):
        api = FakeApi(payload=_payload(observed_at="2020-01-01T00:00:00Z", verified=True))
        evidence = await fetch(api=api)
        assert evidence["observed_at"] == "2026-10-23T12:00:00+00:00"


class TestIdentityMismatch:
    @pytest.mark.asyncio
    async def test_moved_repository_is_reported_unverified_with_its_real_url(self):
        moved = "https://github.com/someone/else/pull/415"
        evidence = await fetch(api=FakeApi(payload=_payload(html_url=moved)))
        assert evidence["url"] == moved
        assert evidence["verified"] is False

    @pytest.mark.asyncio
    async def test_number_mismatch_is_unverified(self):
        evidence = await fetch(api=FakeApi(payload=_payload(number=416)))
        assert evidence["verified"] is False

    @pytest.mark.asyncio
    async def test_head_sha_is_reported_as_observed_not_as_requested(self):
        moved_head = "c" * 40
        evidence = await fetch(api=FakeApi(payload=_payload(head={"sha": moved_head})))
        assert evidence["head_sha"] == moved_head
        assert evidence["verified"] is True  # the fetch was sound; the head simply moved


class TestApiErrors:
    @pytest.mark.parametrize("status", [301, 302, 304, 401, 403, 404, 410, 422, 500, 502])
    @pytest.mark.asyncio
    async def test_non_200_status_fails(self, status):
        with pytest.raises(PullRequestLookupError, match=f"returned {status}"):
            await fetch(api=FakeApi(status=status))

    @pytest.mark.parametrize(
        "error",
        [aiohttp.ClientConnectionError("down"), TimeoutError(), ValueError("bad json")],
    )
    @pytest.mark.asyncio
    async def test_transport_and_decode_errors_fail(self, error):
        with pytest.raises(PullRequestLookupError, match="request failed"):
            await fetch(api=FakeApi(error=error))

    @pytest.mark.parametrize(
        "payload",
        [
            [],
            "nope",
            _payload(head=None),
            _payload(head={}),
            _payload(head={"sha": "a" * 7}),
            _payload(head={"sha": "z" * 40}),
            _payload(html_url=None),
            _payload(html_url="https://gitlab.com/tamaw/progena/pull/415"),
        ],
    )
    @pytest.mark.asyncio
    async def test_malformed_responses_fail(self, payload):
        with pytest.raises(PullRequestLookupError):
            await fetch(api=FakeApi(payload=payload))

    @pytest.mark.parametrize(
        "url",
        [
            "https://gitlab.com/tamaw/progena/pull/415",
            "https://github.example.com/tamaw/progena/pull/415",
            "http://github.com/tamaw/progena/pull/415",
            "https://github.com/tamaw/progena/issues/415",
            "https://github.com/tamaw/progena/pull/415?x=1",
            None,
        ],
    )
    @pytest.mark.asyncio
    async def test_unsupported_urls_are_never_requested(self, url):
        api = FakeApi()
        with pytest.raises(PullRequestLookupError, match="not a github.com pull request"):
            await fetch(url, api)
        assert api.calls == []
