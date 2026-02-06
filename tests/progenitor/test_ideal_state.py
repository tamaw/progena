"""
Comprehensive tests for IdealState domain models and IdealStateBuilder.

Tests cover:
- Domain model behavior (IdealState, SuccessCriterion, etc.)
- IdealStateBuilder structured LLM output integration
- Response model validation
- Error handling and fallbacks
- New LLM-powered methods (anti-criteria extraction, validation, verification)
- Conversion helpers between specs and domain models
"""

import json

import pytest
from unittest.mock import AsyncMock

from app.progenitor.state.ideal_state import (
    AntiCriterion,
    CriterionType,
    IdealState,
    IdealStateBuilder,
    QualityBar,
    StopCondition,
    SuccessCriterion,
    VerificationMethod,
    _parse_criterion_type,
    _spec_to_anti_criterion,
    _spec_to_success_criterion,
)
from app.progenitor.llm.response_models import (
    AntiCriteriaExtractionResponse,
    AntiCriterionSpec,
    CriteriaIssue,
    CriteriaValidationResponse,
    IdealStateClarificationResponse,
    IdealStateExtractionResponse,
    IdealStateRefinementResponse,
    QualityBarSpec,
    StopConditionSpec,
    SuccessCriterionSpec,
    TightenedCriterionSpec,
    VerificationMethodSpec,
    VerificationMethodSuggestionResponse,
)


# ============================================================================
# Fixtures
# ============================================================================


def _make_extraction_response(**overrides) -> dict:
    """Build a valid IdealStateExtractionResponse dict."""
    base = {
        "success_criteria": [
            {
                "id": "criterion_1",
                "description": "Task produces correct output",
                "criterion_type": "functional",
                "rank": 1,
                "measurement": "Output matches expected format",
                "target_score": 0.8,
                "weight": 1.0,
            },
            {
                "id": "criterion_2",
                "description": "Output is concise",
                "criterion_type": "quality",
                "rank": 2,
                "measurement": "Less than 500 words",
                "target_score": 0.7,
                "weight": 0.5,
            },
        ],
        "anti_criteria": [
            {
                "id": "anti_1",
                "description": "Avoid verbose output",
                "severity": 0.8,
                "detection": "Word count check",
            },
        ],
        "quality_bar": {
            "description": "Clear, actionable advice",
            "examples": ["Concise one-paragraph answer"],
            "anti_examples": ["Wall of text with no structure"],
        },
        "verification_methods": [
            {
                "method_type": "heuristic",
                "description": "Check output length and format",
                "automated": True,
            },
        ],
        "stop_conditions": [
            {"condition_type": "min_score", "threshold": 0.8},
            {"condition_type": "max_iterations", "threshold": 5},
        ],
        "constraints": ["Must be Python 3.11 compatible"],
        "assumptions": ["User has basic Python knowledge"],
    }
    base.update(overrides)
    return base


def _make_refinement_response(**overrides) -> dict:
    """Build a valid IdealStateRefinementResponse dict."""
    base = {
        "new_success_criteria": [],
        "tightened_criteria": [],
        "new_anti_criteria": [],
        "updated_quality_description": "",
        "updated_quality_examples": [],
        "updated_quality_anti_examples": [],
        "reasoning": "No refinements needed",
    }
    base.update(overrides)
    return base


def _make_clarification_response(**overrides) -> dict:
    """Build a valid IdealStateClarificationResponse dict."""
    base = {
        "updated_task_description": "",
        "new_success_criteria": [],
        "new_anti_criteria": [],
        "additional_constraints": [],
        "reasoning": "No changes from clarification",
    }
    base.update(overrides)
    return base


def _make_ideal_state(**overrides) -> IdealState:
    """Build a basic IdealState for testing."""
    defaults = {
        "task_description": "Build a weather API client",
        "success_criteria": [
            SuccessCriterion(
                id="criterion_1",
                description="API client works",
                measurement="Returns weather data",
                target_score=0.8,
            ),
        ],
        "stop_conditions": [
            StopCondition(condition_type="min_score", threshold=0.8),
            StopCondition(condition_type="max_iterations", threshold=5),
        ],
    }
    defaults.update(overrides)
    return IdealState(**defaults)


class SimpleMockLLM:
    """Minimal mock LLM client for testing IdealStateBuilder.

    Only exposes chat() as an AsyncMock. Does NOT have _get_json_response_format,
    so the builder falls through to _build_json_schema_format.
    """

    def __init__(self, chat_return_value: str):
        self.chat = AsyncMock(return_value=chat_return_value)


class FailingMockLLM:
    """Mock LLM client where chat() raises an exception."""

    def __init__(self, error: Exception):
        self.chat = AsyncMock(side_effect=error)


def _make_mock_llm(return_value: dict) -> SimpleMockLLM:
    """Create a mock LLM client returning JSON from chat()."""
    return SimpleMockLLM(json.dumps(return_value))


# ============================================================================
# Response Model Tests
# ============================================================================


class TestResponseModelValidation:
    """Tests for the new Pydantic response models used in structured output."""

    def test_success_criterion_spec_defaults(self):
        spec = SuccessCriterionSpec()
        assert spec.criterion_type == "functional"
        assert spec.target_score == 0.8
        assert spec.weight == 1.0

    def test_success_criterion_spec_score_clamping(self):
        spec = SuccessCriterionSpec(target_score=1.5)
        assert spec.target_score == 1.0
        spec = SuccessCriterionSpec(target_score=-0.5)
        assert spec.target_score == 0.0

    def test_success_criterion_spec_null_score(self):
        spec = SuccessCriterionSpec.model_validate({"target_score": None})
        assert spec.target_score == 0.8

    def test_anti_criterion_spec_severity_clamping(self):
        spec = AntiCriterionSpec(severity=3.0)
        assert spec.severity == 2.0
        spec = AntiCriterionSpec(severity=-1.0)
        assert spec.severity == 0.0

    def test_stop_condition_spec_threshold_validation(self):
        spec = StopConditionSpec(threshold=-5)
        assert spec.threshold == 0.0

    def test_tightened_criterion_spec_score_clamping(self):
        spec = TightenedCriterionSpec(criterion_id="c1", updated_target_score=2.0)
        assert spec.updated_target_score == 1.0

    def test_criteria_issue_defaults(self):
        issue = CriteriaIssue(criterion_id="c1")
        assert issue.issue_type == "vague"
        assert issue.description == ""
        assert issue.suggested_fix == ""

    def test_extraction_response_full_parse(self):
        data = _make_extraction_response()
        response = IdealStateExtractionResponse.model_validate(data)
        assert len(response.success_criteria) == 2
        assert len(response.anti_criteria) == 1
        assert response.quality_bar.description == "Clear, actionable advice"
        assert len(response.stop_conditions) == 2
        assert len(response.constraints) == 1

    def test_extraction_response_empty(self):
        response = IdealStateExtractionResponse.model_validate({})
        assert response.success_criteria == []
        assert response.quality_bar.description == "High quality output"

    def test_refinement_response_with_tightenings(self):
        data = _make_refinement_response(
            tightened_criteria=[
                {"criterion_id": "c1", "updated_measurement": "Stricter check",
                 "updated_target_score": 0.9, "reason": "Was too loose"},
            ],
            new_anti_criteria=[
                {"id": "anti_new", "description": "Avoid fluff",
                 "severity": 1.0, "detection": "Fluff detector"},
            ],
        )
        response = IdealStateRefinementResponse.model_validate(data)
        assert len(response.tightened_criteria) == 1
        assert response.tightened_criteria[0].updated_target_score == 0.9
        assert len(response.new_anti_criteria) == 1

    def test_clarification_response_parse(self):
        data = _make_clarification_response(
            updated_task_description="Build a REST API client for weather data",
            new_success_criteria=[
                {"id": "c_clarified", "description": "Uses REST API",
                 "criterion_type": "functional", "rank": 2,
                 "measurement": "HTTP calls verified", "target_score": 0.8, "weight": 1.0},
            ],
            additional_constraints=["Must use requests library"],
        )
        response = IdealStateClarificationResponse.model_validate(data)
        assert "REST" in response.updated_task_description
        assert len(response.new_success_criteria) == 1
        assert len(response.additional_constraints) == 1

    def test_criteria_validation_response_quality_clamping(self):
        response = CriteriaValidationResponse.model_validate(
            {"overall_quality_score": 5.0}
        )
        assert response.overall_quality_score == 1.0

    def test_verification_suggestion_response_parse(self):
        data = {
            "verification_methods": [
                {"method_type": "test", "description": "Unit test", "automated": True},
                {"method_type": "llm_judge", "description": "Quality check", "automated": True},
            ],
            "reasoning": "Automated checks preferred",
        }
        response = VerificationMethodSuggestionResponse.model_validate(data)
        assert len(response.verification_methods) == 2

    def test_anti_criteria_extraction_response(self):
        data = {
            "anti_criteria": [
                {"id": "anti_v1", "description": "Avoid timeout", "severity": 1.5,
                 "detection": "Monitor response time"},
            ],
            "reasoning": "Timeout pattern detected",
        }
        response = AntiCriteriaExtractionResponse.model_validate(data)
        assert len(response.anti_criteria) == 1
        assert response.anti_criteria[0].severity == 1.5


# ============================================================================
# Conversion Helper Tests
# ============================================================================


class TestConversionHelpers:
    """Tests for spec-to-domain model conversion functions."""

    def test_parse_criterion_type_valid(self):
        assert _parse_criterion_type("functional") == CriterionType.FUNCTIONAL
        assert _parse_criterion_type("quality") == CriterionType.QUALITY
        assert _parse_criterion_type("performance") == CriterionType.PERFORMANCE
        assert _parse_criterion_type("security") == CriterionType.SECURITY

    def test_parse_criterion_type_invalid_fallback(self):
        assert _parse_criterion_type("unknown") == CriterionType.FUNCTIONAL
        assert _parse_criterion_type("") == CriterionType.FUNCTIONAL

    def test_spec_to_success_criterion(self):
        spec = SuccessCriterionSpec(
            id="c1",
            description="Test criterion",
            criterion_type="quality",
            rank=2,
            measurement="Visual inspection",
            target_score=0.9,
            weight=1.5,
        )
        result = _spec_to_success_criterion(spec)
        assert isinstance(result, SuccessCriterion)
        assert result.id == "c1"
        assert result.criterion_type == CriterionType.QUALITY
        assert result.target_score == 0.9
        assert result.weight == 1.5

    def test_spec_to_anti_criterion(self):
        spec = AntiCriterionSpec(
            id="a1",
            description="Avoid crashes",
            severity=1.5,
            detection="Error monitoring",
        )
        result = _spec_to_anti_criterion(spec)
        assert isinstance(result, AntiCriterion)
        assert result.id == "a1"
        assert result.severity == 1.5


# ============================================================================
# Domain Model Tests
# ============================================================================


class TestIdealStateDomainModel:
    """Tests for IdealState behavior."""

    def test_create_basic_ideal_state(self):
        state = _make_ideal_state()
        assert state.version == 1
        assert state.task_description == "Build a weather API client"
        assert len(state.success_criteria) == 1

    def test_refine_increments_version(self):
        state = _make_ideal_state()
        refined = state.refine({"constraints": ["New constraint"]})
        assert refined.version == 2
        assert state.version == 1  # Original unchanged

    def test_add_success_criterion(self):
        state = _make_ideal_state()
        new_criterion = SuccessCriterion(
            id="c_new", description="New", measurement="Check"
        )
        state.add_success_criterion(new_criterion)
        assert len(state.success_criteria) == 2

    def test_add_anti_criterion(self):
        state = _make_ideal_state()
        state.add_anti_criterion(AntiCriterion(
            id="anti_1", description="Avoid X", detection="Check"
        ))
        assert len(state.anti_criteria) == 1

    def test_check_stop_conditions_min_score_met(self):
        state = _make_ideal_state()
        should_stop, reasons, met_ids = state.check_stop_conditions(
            current_score=0.85, iteration=1, elapsed_time=10, tool_calls=5,
        )
        assert should_stop is True
        assert "min_score" in met_ids
        # Original not mutated
        assert state.stop_conditions[0].met is False

    def test_check_stop_conditions_max_iterations_met(self):
        state = _make_ideal_state()
        should_stop, reasons, met_ids = state.check_stop_conditions(
            current_score=0.5, iteration=5, elapsed_time=10, tool_calls=5,
        )
        assert should_stop is True
        assert "max_iterations" in met_ids

    def test_check_stop_conditions_nothing_met(self):
        state = _make_ideal_state()
        should_stop, reasons, met_ids = state.check_stop_conditions(
            current_score=0.5, iteration=1, elapsed_time=10, tool_calls=5,
        )
        assert should_stop is False
        assert met_ids == []

    def test_mark_conditions_met_returns_new_state(self):
        state = _make_ideal_state()
        new_state = state.mark_conditions_met(["min_score"])
        assert new_state.stop_conditions[0].met is True
        assert state.stop_conditions[0].met is False  # Original unchanged

    def test_check_stop_conditions_already_met(self):
        state = _make_ideal_state()
        new_state = state.mark_conditions_met(["min_score"])
        should_stop, reasons, met_ids = new_state.check_stop_conditions(
            current_score=0.5, iteration=1, elapsed_time=10, tool_calls=5,
        )
        assert should_stop is True  # Previously met condition triggers stop


# ============================================================================
# IdealStateBuilder - build_from_task Tests
# ============================================================================


class TestBuildFromTask:
    """Tests for IdealStateBuilder.build_from_task()."""

    @pytest.mark.asyncio
    async def test_build_from_task_structured_output(self):
        """Test successful extraction via structured output."""
        mock_llm = _make_mock_llm(_make_extraction_response())
        builder = IdealStateBuilder(mock_llm)

        state = await builder.build_from_task("Build a weather API client")

        assert state.task_description == "Build a weather API client"
        assert len(state.success_criteria) == 2
        assert state.success_criteria[0].id == "criterion_1"
        assert state.success_criteria[0].criterion_type == CriterionType.FUNCTIONAL
        assert state.success_criteria[1].criterion_type == CriterionType.QUALITY
        assert len(state.anti_criteria) == 1
        assert state.quality_bar.description == "Clear, actionable advice"
        assert len(state.verification_methods) == 1
        assert len(state.stop_conditions) == 2
        assert state.constraints == ["Must be Python 3.11 compatible"]
        assert state.assumptions == ["User has basic Python knowledge"]

    @pytest.mark.asyncio
    async def test_build_from_task_with_context(self):
        """Test that context is passed to the prompt."""
        mock_llm = _make_mock_llm(_make_extraction_response())
        builder = IdealStateBuilder(mock_llm)

        context = {"language": "Python", "framework": "FastAPI"}
        await builder.build_from_task("Build an API", context=context)

        # Verify chat was called with prompt containing context
        call_args = mock_llm.chat.call_args
        prompt = call_args[1]["messages"][0]["content"]
        assert "Python" in prompt
        assert "FastAPI" in prompt

    @pytest.mark.asyncio
    async def test_build_from_task_uses_response_format(self):
        """Test that structured output response_format is passed to chat."""
        mock_llm = _make_mock_llm(_make_extraction_response())
        builder = IdealStateBuilder(mock_llm)

        await builder.build_from_task("Build something")

        call_args = mock_llm.chat.call_args
        assert "response_format" in call_args[1]
        rf = call_args[1]["response_format"]
        assert rf["type"] == "json_schema"
        assert rf["json_schema"]["name"] == "ideal_state_extraction"

    @pytest.mark.asyncio
    async def test_build_from_task_fallback_on_llm_error(self):
        """Test graceful fallback when LLM call fails."""
        mock_llm = FailingMockLLM(RuntimeError("API unavailable"))
        builder = IdealStateBuilder(mock_llm)

        state = await builder.build_from_task("Some task")

        assert state.task_description == "Some task"
        assert len(state.success_criteria) == 1
        assert state.success_criteria[0].id == "default_task_completion"

    @pytest.mark.asyncio
    async def test_build_from_task_fallback_on_json_error(self):
        """Test fallback when LLM returns invalid JSON."""
        mock_llm = SimpleMockLLM("This is not JSON")
        builder = IdealStateBuilder(mock_llm)

        state = await builder.build_from_task("Some task")

        assert state.task_description == "Some task"
        assert state.success_criteria[0].id == "default_task_completion"

    @pytest.mark.asyncio
    async def test_build_from_task_unknown_criterion_type(self):
        """Test that unknown criterion types fall back to FUNCTIONAL."""
        data = _make_extraction_response()
        data["success_criteria"][0]["criterion_type"] = "alien_type"
        mock_llm = _make_mock_llm(data)
        builder = IdealStateBuilder(mock_llm)

        state = await builder.build_from_task("Test task")

        assert state.success_criteria[0].criterion_type == CriterionType.FUNCTIONAL

    @pytest.mark.asyncio
    async def test_build_from_task_provider_aware_format(self):
        """Test that _get_json_response_format is used when available."""
        mock_llm = _make_mock_llm(_make_extraction_response())
        mock_llm._get_json_response_format = lambda model, name: {
            "type": "json_object"
        }
        builder = IdealStateBuilder(mock_llm)

        await builder.build_from_task("Test")

        call_args = mock_llm.chat.call_args
        assert call_args[1]["response_format"] == {"type": "json_object"}
        # Should append json instruction for json_object mode
        assert "json" in call_args[1]["system_prompt"].lower()


# ============================================================================
# IdealStateBuilder - refine_from_iteration Tests
# ============================================================================


class TestRefineFromIteration:
    """Tests for IdealStateBuilder.refine_from_iteration()."""

    @pytest.mark.asyncio
    async def test_refine_adds_new_criteria(self):
        data = _make_refinement_response(
            new_success_criteria=[
                {"id": "criterion_refined_1", "description": "New requirement",
                 "criterion_type": "quality", "rank": 3,
                 "measurement": "Quality check", "target_score": 0.85, "weight": 0.8},
            ],
        )
        mock_llm = _make_mock_llm(data)
        builder = IdealStateBuilder(mock_llm)
        initial = _make_ideal_state()

        refined = await builder.refine_from_iteration(initial, {"score": 0.6})

        assert refined.version == initial.version + 1
        assert len(refined.success_criteria) == 2
        assert refined.success_criteria[1].id == "criterion_refined_1"

    @pytest.mark.asyncio
    async def test_refine_tightens_existing_criteria(self):
        data = _make_refinement_response(
            tightened_criteria=[
                {"criterion_id": "criterion_1",
                 "updated_measurement": "Stricter: must return JSON",
                 "updated_target_score": 0.95,
                 "reason": "Original was too loose"},
            ],
        )
        mock_llm = _make_mock_llm(data)
        builder = IdealStateBuilder(mock_llm)
        initial = _make_ideal_state()

        refined = await builder.refine_from_iteration(initial, {"score": 0.6})

        assert refined.success_criteria[0].target_score == 0.95
        assert "Stricter" in refined.success_criteria[0].measurement

    @pytest.mark.asyncio
    async def test_refine_adds_anti_criteria(self):
        data = _make_refinement_response(
            new_anti_criteria=[
                {"id": "anti_refined_1", "description": "Avoid timeout",
                 "severity": 1.5, "detection": "Response time check"},
            ],
        )
        mock_llm = _make_mock_llm(data)
        builder = IdealStateBuilder(mock_llm)
        initial = _make_ideal_state()

        refined = await builder.refine_from_iteration(initial, {"score": 0.6})

        assert len(refined.anti_criteria) == 1
        assert refined.anti_criteria[0].id == "anti_refined_1"

    @pytest.mark.asyncio
    async def test_refine_skips_duplicate_anti_criteria(self):
        data = _make_refinement_response(
            new_anti_criteria=[
                {"id": "existing_anti", "description": "Already exists",
                 "severity": 1.0, "detection": "Check"},
            ],
        )
        mock_llm = _make_mock_llm(data)
        builder = IdealStateBuilder(mock_llm)
        initial = _make_ideal_state(
            anti_criteria=[
                AntiCriterion(id="existing_anti", description="Already there",
                              detection="Existing check"),
            ]
        )

        refined = await builder.refine_from_iteration(initial, {"score": 0.6})

        assert len(refined.anti_criteria) == 1  # No duplicate added

    @pytest.mark.asyncio
    async def test_refine_updates_quality_bar(self):
        data = _make_refinement_response(
            updated_quality_description="Higher quality: must include examples",
            updated_quality_examples=["Good example with code"],
            updated_quality_anti_examples=["Bad: no code shown"],
        )
        mock_llm = _make_mock_llm(data)
        builder = IdealStateBuilder(mock_llm)
        initial = _make_ideal_state()

        refined = await builder.refine_from_iteration(initial, {"score": 0.6})

        assert "Higher quality" in refined.quality_bar.description
        assert "Good example" in refined.quality_bar.examples[0]

    @pytest.mark.asyncio
    async def test_refine_preserves_quality_bar_on_no_update(self):
        data = _make_refinement_response()  # empty quality description
        mock_llm = _make_mock_llm(data)
        builder = IdealStateBuilder(mock_llm)
        original_quality = QualityBar(
            description="Original quality",
            examples=["Original example"],
        )
        initial = _make_ideal_state(quality_bar=original_quality)

        refined = await builder.refine_from_iteration(initial, {"score": 0.6})

        assert refined.quality_bar.description == "Original quality"

    @pytest.mark.asyncio
    async def test_refine_fallback_on_error(self):
        mock_llm = FailingMockLLM(RuntimeError("LLM down"))
        builder = IdealStateBuilder(mock_llm)
        initial = _make_ideal_state()

        refined = await builder.refine_from_iteration(initial, {"score": 0.5})

        assert refined.version == initial.version + 1  # Still incremented


# ============================================================================
# IdealStateBuilder - refine_with_clarification Tests
# ============================================================================


class TestRefineWithClarification:
    """Tests for IdealStateBuilder.refine_with_clarification()."""

    @pytest.mark.asyncio
    async def test_clarification_updates_task_description(self):
        data = _make_clarification_response(
            updated_task_description="Build a REST weather API using FastAPI",
        )
        mock_llm = _make_mock_llm(data)
        builder = IdealStateBuilder(mock_llm)
        initial = _make_ideal_state()

        refined = await builder.refine_with_clarification(initial, {
            "user_clarification": "Use FastAPI framework",
            "original_context": "Framework not specified",
        })

        assert "FastAPI" in refined.task_description
        assert any("FastAPI" in c for c in refined.constraints)

    @pytest.mark.asyncio
    async def test_clarification_adds_criteria_and_constraints(self):
        data = _make_clarification_response(
            new_success_criteria=[
                {"id": "c_clarified_1", "description": "Must use caching",
                 "criterion_type": "performance", "rank": 2,
                 "measurement": "Cache hit rate > 80%", "target_score": 0.8, "weight": 1.0},
            ],
            new_anti_criteria=[
                {"id": "anti_clarified_1", "description": "Avoid raw SQL",
                 "severity": 1.0, "detection": "SQL pattern check"},
            ],
            additional_constraints=["Must use Redis for caching"],
        )
        mock_llm = _make_mock_llm(data)
        builder = IdealStateBuilder(mock_llm)
        initial = _make_ideal_state()

        refined = await builder.refine_with_clarification(initial, {
            "user_clarification": "Add caching with Redis, no raw SQL",
            "original_context": "Performance requirements unclear",
        })

        assert len(refined.success_criteria) == 2
        assert refined.success_criteria[1].criterion_type == CriterionType.PERFORMANCE
        assert len(refined.anti_criteria) == 1
        assert "Redis" in str(refined.constraints)

    @pytest.mark.asyncio
    async def test_clarification_empty_returns_unchanged(self):
        mock_llm = SimpleMockLLM("{}")
        builder = IdealStateBuilder(mock_llm)
        initial = _make_ideal_state()

        result = await builder.refine_with_clarification(initial, {
            "user_clarification": "",
        })

        assert result is initial
        mock_llm.chat.assert_not_called()

    @pytest.mark.asyncio
    async def test_clarification_fallback_records_constraint(self):
        mock_llm = FailingMockLLM(RuntimeError("LLM error"))
        builder = IdealStateBuilder(mock_llm)
        initial = _make_ideal_state()

        refined = await builder.refine_with_clarification(initial, {
            "user_clarification": "Use GraphQL instead",
            "original_context": "API type unclear",
        })

        assert any("GraphQL" in c for c in refined.constraints)


# ============================================================================
# IdealStateBuilder - extract_anti_criteria_from_violations Tests
# ============================================================================


class TestExtractAntiCriteria:
    """Tests for LLM-powered anti-criteria extraction from violations."""

    @pytest.mark.asyncio
    async def test_extracts_anti_criteria(self):
        data = {
            "anti_criteria": [
                {"id": "anti_v1", "description": "Avoid timeout errors",
                 "severity": 1.5, "detection": "Monitor response time"},
                {"id": "anti_v2", "description": "Avoid unhandled exceptions",
                 "severity": 2.0, "detection": "Exception logging"},
            ],
            "reasoning": "Pattern: reliability issues",
        }
        mock_llm = _make_mock_llm(data)
        builder = IdealStateBuilder(mock_llm)
        state = _make_ideal_state()

        result = await builder.extract_anti_criteria_from_violations(
            [{"description": "Request timed out"}, {"description": "Unhandled TypeError"}],
            state,
        )

        assert len(result) == 2
        assert all(isinstance(ac, AntiCriterion) for ac in result)
        assert result[0].severity == 1.5

    @pytest.mark.asyncio
    async def test_skips_existing_anti_criteria(self):
        data = {
            "anti_criteria": [
                {"id": "existing_1", "description": "Already tracked",
                 "severity": 1.0, "detection": "Check"},
                {"id": "new_1", "description": "New issue",
                 "severity": 0.8, "detection": "New check"},
            ],
            "reasoning": "One duplicate, one new",
        }
        mock_llm = _make_mock_llm(data)
        builder = IdealStateBuilder(mock_llm)
        state = _make_ideal_state(
            anti_criteria=[
                AntiCriterion(id="existing_1", description="Existing",
                              detection="Already tracked"),
            ]
        )

        result = await builder.extract_anti_criteria_from_violations(
            [{"description": "Some violation"}], state,
        )

        assert len(result) == 1
        assert result[0].id == "new_1"

    @pytest.mark.asyncio
    async def test_empty_violations_returns_empty(self):
        mock_llm = SimpleMockLLM("{}")
        builder = IdealStateBuilder(mock_llm)
        state = _make_ideal_state()

        result = await builder.extract_anti_criteria_from_violations([], state)

        assert result == []
        mock_llm.chat.assert_not_called()

    @pytest.mark.asyncio
    async def test_llm_error_returns_empty(self):
        mock_llm = FailingMockLLM(RuntimeError("LLM error"))
        builder = IdealStateBuilder(mock_llm)
        state = _make_ideal_state()

        result = await builder.extract_anti_criteria_from_violations(
            [{"description": "Something bad"}], state,
        )

        assert result == []


# ============================================================================
# IdealStateBuilder - validate_criteria Tests
# ============================================================================


class TestValidateCriteria:
    """Tests for LLM-powered criteria validation."""

    @pytest.mark.asyncio
    async def test_valid_criteria_returns_valid(self):
        data = {
            "is_valid": True,
            "issues": [],
            "suggestions": ["Consider adding performance criterion"],
            "overall_quality_score": 0.85,
        }
        mock_llm = _make_mock_llm(data)
        builder = IdealStateBuilder(mock_llm)
        state = _make_ideal_state()

        result = await builder.validate_criteria(state)

        assert result.is_valid is True
        assert result.overall_quality_score == 0.85
        assert len(result.suggestions) == 1

    @pytest.mark.asyncio
    async def test_invalid_criteria_returns_issues(self):
        data = {
            "is_valid": False,
            "issues": [
                {"criterion_id": "criterion_1", "issue_type": "vague",
                 "description": "Measurement too vague",
                 "suggested_fix": "Add specific metrics"},
            ],
            "suggestions": ["Make measurements more specific"],
            "overall_quality_score": 0.3,
        }
        mock_llm = _make_mock_llm(data)
        builder = IdealStateBuilder(mock_llm)
        state = _make_ideal_state()

        result = await builder.validate_criteria(state)

        assert result.is_valid is False
        assert len(result.issues) == 1
        assert result.issues[0].issue_type == "vague"

    @pytest.mark.asyncio
    async def test_validate_criteria_fallback_on_error(self):
        mock_llm = FailingMockLLM(RuntimeError("LLM down"))
        builder = IdealStateBuilder(mock_llm)
        state = _make_ideal_state()

        result = await builder.validate_criteria(state)

        assert result.is_valid is True  # Fallback: assume valid
        assert result.overall_quality_score == 0.5


# ============================================================================
# IdealStateBuilder - suggest_verification_methods Tests
# ============================================================================


class TestSuggestVerificationMethods:
    """Tests for LLM-powered verification method suggestions."""

    @pytest.mark.asyncio
    async def test_suggests_methods(self):
        data = {
            "verification_methods": [
                {"method_type": "test", "description": "Unit test for API calls",
                 "automated": True},
                {"method_type": "llm_judge", "description": "Evaluate output quality",
                 "automated": True},
            ],
            "reasoning": "Mix of automated testing and LLM evaluation",
        }
        mock_llm = _make_mock_llm(data)
        builder = IdealStateBuilder(mock_llm)
        state = _make_ideal_state()

        methods = await builder.suggest_verification_methods(state)

        assert len(methods) == 2
        assert all(isinstance(m, VerificationMethod) for m in methods)
        assert methods[0].method_type == "test"
        assert methods[1].method_type == "llm_judge"

    @pytest.mark.asyncio
    async def test_suggest_with_context(self):
        data = {
            "verification_methods": [
                {"method_type": "test", "description": "Integration test",
                 "automated": True},
            ],
            "reasoning": "Context provided",
        }
        mock_llm = _make_mock_llm(data)
        builder = IdealStateBuilder(mock_llm)
        state = _make_ideal_state()

        await builder.suggest_verification_methods(
            state, context={"infra": "pytest available"}
        )

        call_args = mock_llm.chat.call_args
        prompt = call_args[1]["messages"][0]["content"]
        assert "pytest" in prompt

    @pytest.mark.asyncio
    async def test_suggest_fallback_on_error(self):
        mock_llm = FailingMockLLM(RuntimeError("LLM down"))
        builder = IdealStateBuilder(mock_llm)
        state = _make_ideal_state()

        methods = await builder.suggest_verification_methods(state)

        assert methods == []


# ============================================================================
# IdealStateBuilder - _structured_call Tests
# ============================================================================


class TestStructuredCall:
    """Tests for the core _structured_call helper."""

    @pytest.mark.asyncio
    async def test_passes_response_format(self):
        mock_llm = _make_mock_llm(_make_extraction_response())
        builder = IdealStateBuilder(mock_llm)

        await builder._structured_call(
            prompt="Test",
            system_prompt="System",
            response_model=IdealStateExtractionResponse,
            schema_name="test_schema",
        )

        call_args = mock_llm.chat.call_args
        rf = call_args[1]["response_format"]
        assert rf["type"] == "json_schema"
        assert rf["json_schema"]["strict"] is True

    @pytest.mark.asyncio
    async def test_uses_low_temperature(self):
        mock_llm = _make_mock_llm(_make_extraction_response())
        builder = IdealStateBuilder(mock_llm)

        await builder._structured_call(
            prompt="Test",
            system_prompt="System",
            response_model=IdealStateExtractionResponse,
            schema_name="test",
        )

        call_args = mock_llm.chat.call_args
        assert call_args[1]["temperature"] == 0.3

    @pytest.mark.asyncio
    async def test_json_object_fallback_appends_instruction(self):
        mock_llm = _make_mock_llm(_make_extraction_response())
        mock_llm._get_json_response_format = lambda model, name: {"type": "json_object"}
        builder = IdealStateBuilder(mock_llm)

        await builder._structured_call(
            prompt="Test",
            system_prompt="Original system prompt",
            response_model=IdealStateExtractionResponse,
            schema_name="test",
        )

        call_args = mock_llm.chat.call_args
        assert "json" in call_args[1]["system_prompt"].lower()

    @pytest.mark.asyncio
    async def test_raises_on_invalid_json(self):
        mock_llm = SimpleMockLLM("not json at all")
        builder = IdealStateBuilder(mock_llm)

        with pytest.raises(json.JSONDecodeError):
            await builder._structured_call(
                prompt="Test",
                system_prompt="System",
                response_model=IdealStateExtractionResponse,
                schema_name="test",
            )


# ============================================================================
# IdealStateBuilder - _create_default_ideal_state Tests
# ============================================================================


class TestCreateDefaultIdealState:
    """Tests for the fallback default ideal state."""

    def test_default_has_required_fields(self):
        builder = IdealStateBuilder(llm_client=None)
        state = builder._create_default_ideal_state("Test task")

        assert state.task_description == "Test task"
        assert len(state.success_criteria) == 1
        assert state.success_criteria[0].id == "default_task_completion"
        assert len(state.stop_conditions) == 2
        assert state.stop_conditions[0].condition_type == "min_score"
        assert state.stop_conditions[1].condition_type == "max_iterations"
