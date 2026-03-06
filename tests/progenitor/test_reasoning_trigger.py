"""Tests for reasoning trigger detection and chat node reasoning branch."""

import pytest

from app.progenitor.nodes.chat import _detect_reasoning_trigger


# ---- Trigger detection positive cases ----

@pytest.mark.parametrize(
    "input_text, expected_stripped",
    [
        ("think about how to refactor this", "about how to refactor this"),
        ("Think about scaling", "about scaling"),
        ("THINK why this fails", "why this fails"),
        ("reason through this bug", "through this bug"),
        ("Reason about the edge cases", "the edge cases"),
        ("analyze why this keeps failing", "why this keeps failing"),
        ("Analyze the performance", "the performance"),
        ("consider the tradeoffs of Redis", "the tradeoffs of Redis"),
        ("reflect on the architecture", "on the architecture"),
        ("ponder this design problem", "this design problem"),
        ("evaluate these two approaches", "these two approaches"),
        ("deliberate on the best strategy", "on the best strategy"),
        ("deep dive into the auth flow", "into the auth flow"),
        ("step by step explain how this works", "explain how this works"),
        ("step-by-step explain this", "explain this"),
        ("think through the implications", "the implications"),
        ("think hard about this problem", "about this problem"),
        ("think deeply about scaling", "about scaling"),
    ],
)
def test_detect_trigger_positive(input_text: str, expected_stripped: str):
    stripped, should_reason = _detect_reasoning_trigger(input_text)
    assert should_reason is True
    assert stripped == expected_stripped


# ---- Trigger detection negative cases ----

@pytest.mark.parametrize(
    "input_text",
    [
        "what do you think?",  # "think" not at start
        "I was thinking about this",  # "thinking" not exact match
        "can you help me reason?",  # not at start
        "the analysis shows...",  # "analysis" not "analyze"
        "how to evaluate?",  # not at start
        "hello world",  # no trigger
        "",  # empty
        "considered opinion on this",  # "considered" not "consider"
        "I need to analyze this",  # not at start
    ],
)
def test_detect_trigger_negative(input_text: str):
    stripped, should_reason = _detect_reasoning_trigger(input_text)
    assert should_reason is False
    assert stripped == input_text


def test_bare_keyword_no_trigger():
    """A bare trigger keyword with no query should not activate reasoning."""
    stripped, should_reason = _detect_reasoning_trigger("think")
    assert should_reason is False

    stripped, should_reason = _detect_reasoning_trigger("reason")
    assert should_reason is False


def test_trigger_preserves_content():
    """Trigger stripping should not lose meaningful content."""
    stripped, should_reason = _detect_reasoning_trigger(
        "analyze   why the tests fail intermittently"
    )
    assert should_reason is True
    assert stripped == "why the tests fail intermittently"
