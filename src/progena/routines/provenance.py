"""Deterministic provenance check for a review approval candidate.

A review candidate claims that ACP task ``reviewer_task_id`` reviewed head
``head_sha`` of pull request ``pr_url`` for kanban card ``card_id``. Before a
workflow or routine acts on that claim, the candidate must agree with three
independent records: the kanban card, the completed reviewer ACP task, and a
fresh observation of the pull request (URL, head SHA and verification status).

:func:`validate_review_candidate` is a pure function of its inputs and an
explicit ``now``. It never reads or writes state, calls a model, or touches the
pull request. It fails closed: anything missing, malformed, pending, stale or
mismatched makes the verdict invalid and is reported as a stable reason code.
Reason codes are emitted in a fixed order so equal inputs always give an equal
verdict.

Its records are only as trustworthy as their origin, so the candidate is the
only input a caller supplies. :func:`load_and_validate_review_candidate` reads
the card, the reviewer task and the pull request evidence by the candidate's
own identifiers from a :class:`ReviewEvidenceSource` -- the document store and
a live pull request lookup -- never from caller-writable workflow or routine
state. Without a source the verdict is invalid (``source.unavailable``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

CANDIDATE_FIELDS = ("card_id", "reviewer_task_id", "pr_url", "head_sha")
DEFAULT_MAX_EVIDENCE_AGE = timedelta(minutes=15)
# Evidence stamped slightly ahead of `now` is clock skew; further ahead is not trusted.
_MAX_CLOCK_SKEW = timedelta(seconds=60)
# Full SHA-1 or SHA-256 object ids only; an abbreviated SHA can name more than one commit.
_FULL_SHA = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
_PR_URL = re.compile(
    r"https://(?P<host>[A-Za-z0-9.-]+)/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+)"
    r"/pull/(?P<number>[1-9][0-9]*)/?"
)
_PENDING_TASK_STATUSES = frozenset({"queued", "working"})


@dataclass(frozen=True)
class ProvenanceVerdict:
    """Outcome of :func:`validate_review_candidate`; ``receipt`` is set only when valid."""

    valid: bool
    reasons: tuple[str, ...]
    receipt: dict[str, str] | None

    def as_dict(self) -> dict[str, Any]:
        return {"valid": self.valid, "reasons": list(self.reasons), "receipt": self.receipt}


class ReviewEvidenceSource(Protocol):
    """Trusted reads for a review candidate; each returns None when the record is absent.

    ``get_pull_request_evidence`` performs a live lookup of the pull request and
    returns ``{"url", "head_sha", "verified", "observed_at"}``, stamping
    ``observed_at`` itself at lookup time.
    """

    async def get_kanban_card(self, card_id: str) -> dict[str, Any] | None: ...

    async def get_acp_task(self, task_id: str) -> dict[str, Any] | None: ...

    async def get_pull_request_evidence(self, pr_url: str) -> dict[str, Any] | None: ...


def canonical_pr_url(value: Any) -> str | None:
    """``https://host/owner/repo/pull/N`` with host/owner/repo lowercased, else None."""
    if not isinstance(value, str):
        return None
    match = _PR_URL.fullmatch(value.strip())
    if match is None:
        return None
    host, owner, repo = (match[name].lower() for name in ("host", "owner", "repo"))
    return f"https://{host}/{owner}/{repo}/pull/{int(match['number'])}"


def _full_sha(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    sha = value.strip().lower()
    return sha if _FULL_SHA.fullmatch(sha) else None


def _timestamp(value: Any) -> datetime | None:
    """A timezone-aware ISO 8601 timestamp; naive or unparseable values are None."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _present(value: Any) -> bool:
    return isinstance(value, str) and value.strip() != ""


def validate_review_candidate(
    candidate: Any,
    *,
    card: Any,
    acp_task: Any,
    pr_evidence: Any,
    now: datetime,
    max_evidence_age: timedelta = DEFAULT_MAX_EVIDENCE_AGE,
) -> ProvenanceVerdict:
    """Validate a ``review_candidate`` against its card, reviewer task and PR evidence.

    ``card`` is a ``kanban_card`` document, ``acp_task`` an ``acp_task`` document,
    and ``pr_evidence`` a current observation of the pull request::

        {"url": str, "head_sha": str, "verified": bool | None, "observed_at": ISO 8601}

    ``verified`` must be exactly ``True``; ``None`` or ``"pending"`` means the
    verification has not finished. Inputs are only read, never modified.
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    reasons: list[str] = []

    # --- candidate ---------------------------------------------------------------
    fields = candidate if isinstance(candidate, dict) else {}
    if not isinstance(candidate, dict):
        reasons.append("candidate.missing")
    else:
        reasons.extend(
            f"candidate.{name}.missing"
            for name in CANDIDATE_FIELDS
            if not _present(fields.get(name))
        )
    card_id = fields.get("card_id") if _present(fields.get("card_id")) else None
    task_id = fields.get("reviewer_task_id") if _present(fields.get("reviewer_task_id")) else None
    pr_url = canonical_pr_url(fields.get("pr_url"))
    if _present(fields.get("pr_url")) and pr_url is None:
        reasons.append("candidate.pr_url.malformed")
    head_sha = _full_sha(fields.get("head_sha"))
    if _present(fields.get("head_sha")) and head_sha is None:
        reasons.append("candidate.head_sha.malformed")

    # --- kanban card -------------------------------------------------------------
    if not isinstance(card, dict):
        reasons.append("card.missing")
    else:
        if card_id is None or card.get("id") != card_id:
            reasons.append("card.id.mismatch")
        card_pr_url = card.get("pr_url")
        if not _present(card_pr_url):
            reasons.append("card.pr_url.missing")
        elif pr_url is None or canonical_pr_url(card_pr_url) != pr_url:
            reasons.append("card.pr_url.mismatch")

    # --- reviewer ACP task -------------------------------------------------------
    completed_at: datetime | None = None
    observed_at: datetime | None = None
    if not isinstance(acp_task, dict):
        reasons.append("task.missing")
    else:
        if task_id is None or acp_task.get("task_id") != task_id:
            reasons.append("task.id.mismatch")
        if not _present(acp_task.get("card_id")):
            reasons.append("task.card_id.missing")
        elif card_id is None or acp_task.get("card_id") != card_id:
            reasons.append("task.card_id.mismatch")
        status = acp_task.get("status")
        if (
            status in _PENDING_TASK_STATUSES
            or acp_task.get("pending_input") is not None
            or acp_task.get("cancel_requested") not in (None, False)
        ):
            reasons.append("task.pending")
        elif status != "completed":
            reasons.append("task.not_completed")
        completed_at = _timestamp(acp_task.get("completed_at"))
        if completed_at is None:
            reasons.append("task.completed_at.missing")

    # --- pull request evidence ---------------------------------------------------
    if not isinstance(pr_evidence, dict):
        reasons.append("evidence.missing")
    else:
        evidence_url = pr_evidence.get("url")
        if not _present(evidence_url):
            reasons.append("evidence.url.missing")
        elif canonical_pr_url(evidence_url) is None:
            reasons.append("evidence.url.malformed")
        elif canonical_pr_url(evidence_url) != pr_url:
            reasons.append("evidence.url.mismatch")

        evidence_sha = pr_evidence.get("head_sha")
        if not _present(evidence_sha):
            reasons.append("evidence.head_sha.missing")
        elif _full_sha(evidence_sha) is None:
            reasons.append("evidence.head_sha.malformed")
        elif _full_sha(evidence_sha) != head_sha:
            # The PR head moved since the candidate was reviewed (or never matched).
            reasons.append("evidence.head_sha.mismatch")

        if "verified" not in pr_evidence:
            reasons.append("evidence.verified.missing")
        else:
            verified = pr_evidence["verified"]
            if verified is None or verified == "pending":
                reasons.append("evidence.verified.pending")
            elif verified is False:
                reasons.append("evidence.verified.false")
            elif verified is not True:
                reasons.append("evidence.verified.malformed")

        observed_at = _timestamp(pr_evidence.get("observed_at"))
        if observed_at is None:
            reasons.append("evidence.observed_at.missing")
        elif observed_at > now + _MAX_CLOCK_SKEW:
            reasons.append("evidence.observed_at.future")
        else:
            if now - observed_at > max_evidence_age:
                reasons.append("evidence.stale")
            if completed_at is not None and observed_at < completed_at:
                # Observed before the review finished, so it cannot show the reviewed head
                # is still current.
                reasons.append("evidence.predates_review")

    # Every None below already has a reason; checked again so nothing unvalidated passes.
    if (
        reasons
        or card_id is None
        or task_id is None
        or pr_url is None
        or head_sha is None
        or completed_at is None
        or observed_at is None
    ):
        return ProvenanceVerdict(valid=False, reasons=tuple(reasons), receipt=None)
    return ProvenanceVerdict(
        valid=True,
        reasons=(),
        receipt={
            "card_id": card_id,
            "reviewer_task_id": task_id,
            "pr_url": pr_url,
            "head_sha": head_sha,
            "review_completed_at": completed_at.isoformat(),
            "evidence_observed_at": observed_at.isoformat(),
        },
    )


async def load_and_validate_review_candidate(
    candidate: Any,
    *,
    source: ReviewEvidenceSource | None,
    now: datetime,
    max_evidence_age: timedelta = DEFAULT_MAX_EVIDENCE_AGE,
) -> ProvenanceVerdict:
    """Load the candidate's records from ``source`` by its own ids, then validate.

    Records are never taken from the caller. A missing source, or a read that
    raises, fails closed with a ``source.*`` reason ahead of the validator's own.
    """
    if source is None:
        return ProvenanceVerdict(valid=False, reasons=("source.unavailable",), receipt=None)
    fields = candidate if isinstance(candidate, dict) else {}
    card_id = fields.get("card_id")
    task_id = fields.get("reviewer_task_id")
    pr_url = canonical_pr_url(fields.get("pr_url"))
    reads = (
        ("card", source.get_kanban_card, card_id if _present(card_id) else None),
        ("acp_task", source.get_acp_task, task_id if _present(task_id) else None),
        ("pr_evidence", source.get_pull_request_evidence, pr_url),
    )
    records: dict[str, Any] = {}
    source_reasons: list[str] = []
    for name, read, key in reads:
        records[name] = None
        if key is None:
            continue  # the validator reports the candidate field as missing or malformed
        try:
            records[name] = await read(key)
        except Exception:  # any failed read is missing evidence, never a pass
            source_reasons.append(f"source.{name}.error")
    verdict = validate_review_candidate(
        candidate, now=now, max_evidence_age=max_evidence_age, **records
    )
    if not source_reasons:
        return verdict
    return ProvenanceVerdict(valid=False, reasons=(*source_reasons, *verdict.reasons), receipt=None)
