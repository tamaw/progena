"""Read-only pull request lookup for review provenance.

Fetches one GitHub pull request from the public REST API and reports what was
observed: its canonical URL, full head SHA, whether the observation itself is
trustworthy (``verified``) and when it was made (``observed_at``).

``verified`` is deliberately narrow. It means only that the remote fetch
succeeded and returned this exact pull request with a well-formed full head
SHA. It says nothing about CI, checks, reviews, mergeability or signatures.

Requests follow the plugin installer's convention (unauthenticated ``aiohttp``
GETs to ``api.github.com``); no GitHub credential is read, so private
repositories return 404 and fail closed. Only ``github.com`` pull request URLs
are supported. Nothing is ever written to the forge.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

import aiohttp

_API_ROOT = "https://api.github.com"
_TIMEOUT = aiohttp.ClientTimeout(total=15)
_HEADERS = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
_FULL_SHA = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
_GITHUB_PR_URL = re.compile(
    r"https://github\.com/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+)"
    r"/pull/(?P<number>[1-9][0-9]*)/?"
)

# GET a URL; returns (HTTP status, decoded JSON body or None).
JsonGetter = Callable[[str], Awaitable[tuple[int, Any]]]


class PullRequestLookupError(Exception):
    """The pull request could not be observed; callers must treat it as missing evidence."""


def _canonical(url: Any) -> tuple[str, str, int] | None:
    if not isinstance(url, str):
        return None
    match = _GITHUB_PR_URL.fullmatch(url.strip())
    if match is None:
        return None
    return match["owner"].lower(), match["repo"].lower(), int(match["number"])


def _render(owner: str, repo: str, number: int) -> str:
    return f"https://github.com/{owner}/{repo}/pull/{number}"


async def _get_json(url: str) -> tuple[int, Any]:
    async with (
        aiohttp.ClientSession(timeout=_TIMEOUT, headers=_HEADERS) as session,
        session.get(url, allow_redirects=False) as response,
    ):
        if response.status != 200:
            return response.status, None
        return response.status, await response.json(content_type=None)


async def fetch_pull_request_evidence(
    pr_url: str,
    *,
    get_json: JsonGetter = _get_json,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> dict[str, Any]:
    """Observe ``pr_url`` and return ``{url, head_sha, verified, observed_at}``.

    Raises :class:`PullRequestLookupError` when the URL is not a github.com pull
    request, the API call fails or redirects, or the response is malformed. A
    response for a *different* pull request (e.g. a moved repository) is
    returned with that pull request's URL and ``verified`` false, so the
    validator reports the identity mismatch instead of trusting it.
    """
    requested = _canonical(pr_url)
    if requested is None:
        raise PullRequestLookupError(f"not a github.com pull request URL: {pr_url!r}")
    owner, repo, number = requested
    api_url = f"{_API_ROOT}/repos/{quote(owner)}/{quote(repo)}/pulls/{number}"
    try:
        status, payload = await get_json(api_url)
    except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
        raise PullRequestLookupError(f"GitHub API request failed: {exc}") from exc
    if status != 200:
        raise PullRequestLookupError(f"GitHub API returned {status} for {_render(*requested)}")
    observed_at = now()
    if not isinstance(payload, dict):
        raise PullRequestLookupError("GitHub API returned a non-object pull request")

    head = payload.get("head")
    head_sha = head.get("sha") if isinstance(head, dict) else None
    if not isinstance(head_sha, str) or _FULL_SHA.fullmatch(head_sha.lower()) is None:
        raise PullRequestLookupError("GitHub pull request has no full head SHA")
    observed = _canonical(payload.get("html_url"))
    if observed is None:
        raise PullRequestLookupError("GitHub pull request has no github.com html_url")

    same_pull_request = observed == requested and payload.get("number") == number
    return {
        "url": _render(*observed),
        "head_sha": head_sha.lower(),
        "verified": same_pull_request,
        "observed_at": observed_at.isoformat(),
    }
