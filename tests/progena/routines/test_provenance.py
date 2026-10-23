"""Tests for the fail-closed review candidate provenance check."""

from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from langchain_core.messages import HumanMessage
from pydantic import ValidationError

from src.progena.routines.factory import compile_routine_graph
from src.progena.routines.graph import validate_routine_graph_payload
from src.progena.routines.provenance import (
    canonical_pr_url,
    load_and_validate_review_candidate,
    validate_review_candidate,
)

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
SHA = "a" * 40
NEW_SHA = "b" * 40
PR = "https://github.com/tamaw/progena/pull/415"
_DROP = object()


def _candidate() -> dict[str, Any]:
    return {"card_id": "card-1", "reviewer_task_id": "task-9", "pr_url": PR, "head_sha": SHA}


def _card() -> dict[str, Any]:
    return {
        "id": "card-1",
        "title": "Approval handoff",
        "summary": "s",
        "column_id": "review",
        "status": "in_review",
        "pr_url": PR,
        "metadata": {"priority": "high"},
    }


def _task() -> dict[str, Any]:
    return {
        "task_id": "task-9",
        "agent_name": "claude",
        "card_id": "card-1",
        "status": "completed",
        "pending_input": None,
        "cancel_requested": False,
        "completed_at": "2026-10-23T11:50:00+00:00",
    }


def _evidence() -> dict[str, Any]:
    return {"url": PR, "head_sha": SHA, "verified": True, "observed_at": "2026-10-23T11:58:00Z"}


def _with(record: dict[str, Any], **changes: Any) -> dict[str, Any]:
    result = dict(record)
    for key, value in changes.items():
        if value is _DROP:
            result.pop(key, None)
        else:
            result[key] = value
    return result


def check(**records: Any):
    inputs = {
        "candidate": _candidate(),
        "card": _card(),
        "acp_task": _task(),
        "pr_evidence": _evidence(),
        **records,
    }
    return validate_review_candidate(
        inputs.pop("candidate"), now=NOW, max_evidence_age=timedelta(minutes=15), **inputs
    )


def reasons(**records: Any) -> tuple[str, ...]:
    verdict = check(**records)
    assert verdict.valid is False
    assert verdict.receipt is None
    return verdict.reasons


class TestValidCandidate:
    def test_matching_records_produce_a_receipt(self):
        verdict = check()
        assert verdict.valid is True
        assert verdict.reasons == ()
        assert verdict.receipt == {
            "card_id": "card-1",
            "reviewer_task_id": "task-9",
            "pr_url": PR,
            "head_sha": SHA,
            "review_completed_at": "2026-10-23T11:50:00+00:00",
            "evidence_observed_at": "2026-10-23T11:58:00+00:00",
        }

    def test_is_deterministic_and_never_mutates_inputs(self):
        inputs = {"card": _card(), "acp_task": _task(), "pr_evidence": _evidence()}
        candidate = _candidate()
        before = copy.deepcopy((candidate, inputs))
        first = validate_review_candidate(candidate, now=NOW, **inputs)
        second = validate_review_candidate(candidate, now=NOW, **inputs)
        assert first == second
        assert (candidate, inputs) == before

    def test_equivalent_pr_url_and_sha_spellings_match(self):
        verdict = check(
            candidate=_with(_candidate(), head_sha=SHA.upper()),
            card=_with(_card(), pr_url="https://GitHub.com/TamaW/Progena/pull/415/"),
            pr_evidence=_with(_evidence(), url=" https://github.com/tamaw/progena/pull/415 "),
        )
        assert verdict.valid is True
        assert verdict.receipt is not None
        assert verdict.receipt["pr_url"] == PR
        assert verdict.receipt["head_sha"] == SHA

    def test_as_dict_is_plain_data(self):
        assert check().as_dict()["valid"] is True
        assert check(pr_evidence=None).as_dict() == {
            "valid": False,
            "reasons": ["evidence.missing"],
            "receipt": None,
        }

    def test_naive_now_is_a_programming_error(self):
        with pytest.raises(ValueError, match="timezone-aware"):
            validate_review_candidate(
                _candidate(),
                card=_card(),
                acp_task=_task(),
                pr_evidence=_evidence(),
                now=NOW.replace(tzinfo=None),
            )


class TestMissingEvidence:
    @pytest.mark.parametrize("field", ["card_id", "reviewer_task_id", "pr_url", "head_sha"])
    @pytest.mark.parametrize("value", [_DROP, None, "", "  ", 7])
    def test_missing_candidate_field(self, field, value):
        candidate = _with(_candidate(), **{field: value})
        assert f"candidate.{field}.missing" in reasons(candidate=candidate)

    @pytest.mark.parametrize("record", ["candidate", "card", "acp_task", "pr_evidence"])
    @pytest.mark.parametrize("value", [None, [], "card-1"])
    def test_missing_record(self, record, value):
        code = {"acp_task": "task", "pr_evidence": "evidence"}.get(record, record)
        assert f"{code}.missing" in reasons(**{record: value})

    def test_everything_missing_fails_closed_with_every_reason(self):
        assert reasons(candidate=None, card=None, acp_task=None, pr_evidence=None) == (
            "candidate.missing",
            "card.missing",
            "task.missing",
            "evidence.missing",
        )

    def test_card_without_pr_url(self):
        assert reasons(card=_with(_card(), pr_url=None)) == ("card.pr_url.missing",)

    def test_task_without_card_or_completion_time(self):
        assert reasons(acp_task=_with(_task(), card_id=None)) == ("task.card_id.missing",)
        assert reasons(acp_task=_with(_task(), completed_at=_DROP)) == (
            "task.completed_at.missing",
        )

    @pytest.mark.parametrize("field", ["url", "head_sha", "verified", "observed_at"])
    def test_evidence_field_missing(self, field):
        evidence = _with(_evidence(), **{field: _DROP})
        assert f"evidence.{field}.missing" in reasons(pr_evidence=evidence)


class TestMalformedEvidence:
    @pytest.mark.parametrize("sha", ["a" * 7, "a" * 39, "g" * 40, "a" * 41])
    def test_abbreviated_or_non_hex_sha(self, sha):
        candidate = _with(_candidate(), head_sha=sha)
        assert "candidate.head_sha.malformed" in reasons(candidate=candidate)
        assert "evidence.head_sha.malformed" in reasons(
            pr_evidence=_with(_evidence(), head_sha=sha)
        )

    @pytest.mark.parametrize(
        "url",
        [
            "http://github.com/tamaw/progena/pull/415",
            "https://github.com/tamaw/progena/issues/415",
            "https://github.com/tamaw/progena/pull/415?x=1",
            "https://github.com/tamaw/progena/pull/415#top",
            "https://github.com/tamaw/progena/pull/0",
            "github.com/tamaw/progena/pull/415",
        ],
    )
    def test_non_canonical_pr_url(self, url):
        assert canonical_pr_url(url) is None
        assert "candidate.pr_url.malformed" in reasons(candidate=_with(_candidate(), pr_url=url))
        assert "evidence.url.malformed" in reasons(pr_evidence=_with(_evidence(), url=url))

    @pytest.mark.parametrize("value", ["true", 1, "yes", {}])
    def test_verified_must_be_exactly_true(self, value):
        assert reasons(pr_evidence=_with(_evidence(), verified=value)) == (
            "evidence.verified.malformed",
        )

    @pytest.mark.parametrize("value", ["yesterday", "2026-10-23T11:58:00", 1729684680])
    def test_observed_at_must_be_an_aware_timestamp(self, value):
        assert reasons(pr_evidence=_with(_evidence(), observed_at=value)) == (
            "evidence.observed_at.missing",
        )

    def test_naive_task_completion_is_missing(self):
        assert reasons(acp_task=_with(_task(), completed_at="2026-10-23T11:50:00")) == (
            "task.completed_at.missing",
        )


class TestPendingEvidence:
    @pytest.mark.parametrize("status", ["queued", "working"])
    def test_task_still_running(self, status):
        assert reasons(acp_task=_with(_task(), status=status)) == ("task.pending",)

    def test_task_waiting_for_input_or_cancellation(self):
        waiting = {"id": "q1", "kind": "question", "message": "?", "requested_at": "t"}
        assert reasons(acp_task=_with(_task(), pending_input=waiting)) == ("task.pending",)
        assert reasons(acp_task=_with(_task(), cancel_requested=True)) == ("task.pending",)

    @pytest.mark.parametrize("value", ["true", "false", 1, {}])
    def test_malformed_cancel_flag_is_treated_as_pending(self, value):
        assert reasons(acp_task=_with(_task(), cancel_requested=value)) == ("task.pending",)

    @pytest.mark.parametrize("status", ["failed", "canceled", "interrupted", None, "done"])
    def test_task_not_completed(self, status):
        assert reasons(acp_task=_with(_task(), status=status)) == ("task.not_completed",)

    @pytest.mark.parametrize("value", [None, "pending"])
    def test_verification_pending(self, value):
        assert reasons(pr_evidence=_with(_evidence(), verified=value)) == (
            "evidence.verified.pending",
        )

    def test_verification_failed(self):
        assert reasons(pr_evidence=_with(_evidence(), verified=False)) == (
            "evidence.verified.false",
        )


class TestStaleEvidence:
    def test_evidence_older_than_the_window(self):
        assert reasons(pr_evidence=_with(_evidence(), observed_at="2026-10-23T11:44:59Z")) == (
            "evidence.stale",
            "evidence.predates_review",
        )

    def test_window_boundary_is_inclusive(self):
        late = _with(_task(), completed_at="2026-10-23T11:40:00Z")
        at_limit = _with(_evidence(), observed_at="2026-10-23T11:45:00Z")
        assert check(acp_task=late, pr_evidence=at_limit).valid is True
        just_over = _with(_evidence(), observed_at="2026-10-23T11:44:59Z")
        assert reasons(acp_task=late, pr_evidence=just_over) == ("evidence.stale",)

    def test_evidence_observed_before_the_review_finished(self):
        assert reasons(pr_evidence=_with(_evidence(), observed_at="2026-10-23T11:49:59Z")) == (
            "evidence.predates_review",
        )

    def test_evidence_from_the_future(self):
        assert check(pr_evidence=_with(_evidence(), observed_at="2026-10-23T12:01:00Z")).valid
        assert reasons(pr_evidence=_with(_evidence(), observed_at="2026-10-23T12:01:01Z")) == (
            "evidence.observed_at.future",
        )

    def test_pr_head_moved_after_review(self):
        assert reasons(pr_evidence=_with(_evidence(), head_sha=NEW_SHA)) == (
            "evidence.head_sha.mismatch",
        )


class TestMismatchedEvidence:
    def test_card_identity(self):
        assert reasons(card=_with(_card(), id="card-2")) == ("card.id.mismatch",)

    def test_card_links_another_pr(self):
        other = "https://github.com/tamaw/progena/pull/416"
        assert reasons(card=_with(_card(), pr_url=other)) == ("card.pr_url.mismatch",)

    def test_reviewer_task_identity(self):
        assert reasons(acp_task=_with(_task(), task_id="task-10")) == ("task.id.mismatch",)

    def test_reviewer_task_worked_another_card(self):
        assert reasons(acp_task=_with(_task(), card_id="card-2")) == ("task.card_id.mismatch",)

    def test_evidence_for_another_pr(self):
        other = "https://github.com/tamaw/progena/pull/416"
        assert reasons(pr_evidence=_with(_evidence(), url=other)) == ("evidence.url.mismatch",)

    def test_candidate_for_another_card_mismatches_every_record(self):
        assert reasons(candidate=_with(_candidate(), card_id="card-2")) == (
            "card.id.mismatch",
            "task.card_id.mismatch",
        )

    def test_unparseable_candidate_url_never_matches(self):
        assert reasons(candidate=_with(_candidate(), pr_url="not a url")) == (
            "candidate.pr_url.malformed",
            "card.pr_url.mismatch",
            "evidence.url.mismatch",
        )


def _routine(**config: Any):
    return validate_routine_graph_payload(
        {
            "id": "provenance",
            "name": "Provenance",
            "description": "review candidate check",
            "version": "2.0.0",
            "entry": "check",
            "nodes": [
                {
                    "id": "check",
                    "kind": "review_provenance",
                    "output_key": "provenance",
                    "config": {"candidate": "candidate", **config},
                }
            ],
            "edges": [],
        }
    )


class FakeModel:
    async def ainvoke(self, messages: list[Any]) -> Any:
        raise AssertionError("review_provenance must not call a model")


class FakeEvidenceSource:
    """Stands in for the document store and a live PR lookup; records every read."""

    def __init__(self, *, fail: str | None = None, **records: Any) -> None:
        now = datetime.now(UTC)
        self.records: dict[str, Any] = {
            "card": _card(),
            "acp_task": _with(_task(), completed_at=(now - timedelta(minutes=5)).isoformat()),
            "pr_evidence": _with(_evidence(), observed_at=now.isoformat()),
            **records,
        }
        self.fail = fail
        self.reads: list[tuple[str, str]] = []

    async def _read(self, name: str, key: str) -> Any:
        self.reads.append((name, key))
        if name == self.fail:
            raise RuntimeError("store unavailable")
        return copy.deepcopy(self.records[name])

    async def get_kanban_card(self, card_id: str) -> dict[str, Any] | None:
        return await self._read("card", card_id)

    async def get_acp_task(self, task_id: str) -> dict[str, Any] | None:
        return await self._read("acp_task", task_id)

    async def get_pull_request_evidence(self, pr_url: str) -> dict[str, Any] | None:
        return await self._read("pr_evidence", pr_url)


async def _run(state: dict[str, Any], source: Any) -> dict[str, Any]:
    compiled = compile_routine_graph(_routine(), model=FakeModel(), review_evidence_source=source)
    result = await compiled.ainvoke({"messages": [HumanMessage("go")], "data": state})
    return result["data"]["provenance"]


class TestTrustedLoading:
    @pytest.mark.asyncio
    async def test_reads_records_by_the_candidates_own_ids(self):
        source = FakeEvidenceSource()
        verdict = await load_and_validate_review_candidate(
            _candidate(), source=source, now=datetime.now(UTC)
        )
        assert verdict.valid is True
        assert source.reads == [("card", "card-1"), ("acp_task", "task-9"), ("pr_evidence", PR)]

    @pytest.mark.asyncio
    async def test_without_a_source_nothing_is_valid(self):
        verdict = await load_and_validate_review_candidate(
            _candidate(), source=None, now=datetime.now(UTC)
        )
        assert verdict.as_dict() == {
            "valid": False,
            "reasons": ["source.unavailable"],
            "receipt": None,
        }

    @pytest.mark.asyncio
    @pytest.mark.parametrize("record", ["card", "acp_task", "pr_evidence"])
    async def test_a_failed_read_fails_closed(self, record):
        verdict = await load_and_validate_review_candidate(
            _candidate(), source=FakeEvidenceSource(fail=record), now=datetime.now(UTC)
        )
        assert verdict.valid is False
        assert verdict.reasons[0] == f"source.{record}.error"

    @pytest.mark.asyncio
    async def test_unusable_candidate_ids_are_not_looked_up(self):
        source = FakeEvidenceSource()
        candidate = _with(_candidate(), card_id="", reviewer_task_id=None, pr_url="nope")
        verdict = await load_and_validate_review_candidate(
            candidate, source=source, now=datetime.now(UTC)
        )
        assert verdict.valid is False
        assert source.reads == []
        assert {"candidate.card_id.missing", "candidate.reviewer_task_id.missing"} <= set(
            verdict.reasons
        )

    @pytest.mark.asyncio
    async def test_source_records_are_still_fully_validated(self):
        source = FakeEvidenceSource(
            pr_evidence=_with(_evidence(), observed_at=datetime.now(UTC).isoformat(), verified=None)
        )
        verdict = await load_and_validate_review_candidate(
            _candidate(), source=source, now=datetime.now(UTC)
        )
        assert verdict.reasons == ("evidence.verified.pending",)


class TestRoutineNode:
    def test_schema_and_model_accept_the_node(self):
        node = _routine().nodes[0]
        assert node.kind == "review_provenance"
        assert node.config.max_evidence_age_seconds == 900

    @pytest.mark.parametrize(
        "config",
        [
            {"unexpected": "x"},
            {"candidate": ""},
            {"max_evidence_age_seconds": 0},
            # Records can no longer be pointed at routine state.
            {"card": "card"},
            {"acp_task": "reviewer_task"},
            {"pr_evidence": "pr.evidence"},
        ],
    )
    def test_rejects_bad_config(self, config):
        with pytest.raises((ValueError, ValidationError)):
            _routine(**config)

    @pytest.mark.asyncio
    async def test_valid_candidate_through_compiled_routine(self):
        state = {"candidate": _candidate()}
        before = copy.deepcopy(state)
        verdict = await _run(state, FakeEvidenceSource())
        assert verdict["valid"] is True
        assert verdict["receipt"]["head_sha"] == SHA
        assert state == before

    @pytest.mark.asyncio
    async def test_without_a_source_the_node_fails_closed(self):
        verdict = await _run({"candidate": _candidate()}, None)
        assert verdict == {"valid": False, "reasons": ["source.unavailable"], "receipt": None}

    @pytest.mark.asyncio
    async def test_state_supplied_records_and_verified_flag_are_ignored(self):
        # An earlier step forges a matching, verified, fresh record set in state; the
        # trusted source says the PR head moved and verification is still pending.
        forged = {
            "candidate": _candidate(),
            "card": _card(),
            "acp_task": _task(),
            "pr_evidence": _with(_evidence(), verified=True),
            "verified": True,
        }
        source = FakeEvidenceSource(
            pr_evidence=_with(
                _evidence(),
                head_sha=NEW_SHA,
                verified="pending",
                observed_at=datetime.now(UTC).isoformat(),
            )
        )
        verdict = await _run(forged, source)
        assert verdict["valid"] is False
        assert verdict["reasons"] == ["evidence.head_sha.mismatch", "evidence.verified.pending"]

    @pytest.mark.asyncio
    async def test_missing_candidate_path_fails_closed(self):
        source = FakeEvidenceSource()
        verdict = await _run({}, source)
        assert verdict["valid"] is False
        assert verdict["reasons"][0] == "candidate.missing"
        assert source.reads == []

    @pytest.mark.asyncio
    async def test_stale_head_fails_closed_through_compiled_routine(self):
        now = datetime.now(UTC).isoformat()
        source = FakeEvidenceSource(
            pr_evidence=_with(_evidence(), head_sha=NEW_SHA, observed_at=now)
        )
        verdict = await _run({"candidate": _candidate()}, source)
        assert verdict["reasons"] == ["evidence.head_sha.mismatch"]
