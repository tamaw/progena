"""Tests for LLM response validation models."""

import pytest
from app.progenitor.llm.response_models import (
    DecisionResponse,
    PlannerResponse,
    BuildSpec,
    WorkItemSpec,
    VerificationResult,
    validate_decision_response,
    validate_planner_response,
    validate_build_spec,
    validate_verification_result,
)
from app.progenitor.llm.client import _build_json_schema_format


class TestBuildJsonSchemaFormat:
    """Tests for the structured output schema builder."""

    def test_basic_schema_structure(self):
        """Test that the schema has the correct top-level structure."""
        result = _build_json_schema_format(DecisionResponse, "test_decision")

        assert result["type"] == "json_schema"
        assert "json_schema" in result
        assert result["json_schema"]["name"] == "test_decision"
        assert result["json_schema"]["strict"] is True
        assert "schema" in result["json_schema"]

    def test_schema_has_additional_properties_false(self):
        """Test that additionalProperties is set to false for strict mode."""
        result = _build_json_schema_format(DecisionResponse, "test")
        schema = result["json_schema"]["schema"]

        assert schema.get("additionalProperties") is False

    def test_all_properties_required(self):
        """Test that all properties are marked as required."""
        result = _build_json_schema_format(DecisionResponse, "test")
        schema = result["json_schema"]["schema"]

        if "properties" in schema:
            assert "required" in schema
            assert set(schema["required"]) == set(schema["properties"].keys())

    def test_works_with_planner_response(self):
        """Test schema generation for PlannerResponse (has nested models)."""
        result = _build_json_schema_format(PlannerResponse, "planner")

        assert result["type"] == "json_schema"
        schema = result["json_schema"]["schema"]
        assert schema.get("additionalProperties") is False

    def test_works_with_build_spec(self):
        """Test schema generation for BuildSpec."""
        result = _build_json_schema_format(BuildSpec, "build")

        assert result["type"] == "json_schema"
        schema = result["json_schema"]["schema"]
        assert schema.get("additionalProperties") is False


class TestDecisionResponse:
    """Tests for DecisionResponse validation."""

    def test_valid_decision(self):
        """Test parsing a valid decision response."""
        data = {
            "action": "answer_directly",
            "reasoning": "Simple question about time",
            "requires_planning": False,
            "target_capabilities": [],
            "direct_response": "It is 3pm."
        }
        result = validate_decision_response(data)
        assert result.action == "answer_directly"
        assert result.reasoning == "Simple question about time"
        assert result.requires_planning is False

    def test_action_normalization(self):
        """Test that action values are normalized."""
        # "answer" should become "answer_directly"
        data = {"action": "answer"}
        result = validate_decision_response(data)
        assert result.action == "answer_directly"

        # "direct" should become "answer_directly"
        data = {"action": "direct"}
        result = validate_decision_response(data)
        assert result.action == "answer_directly"

    def test_unknown_action_fallback(self):
        """Test that unknown actions fall back to answer_directly."""
        data = {"action": "invalid_action_xyz"}
        result = validate_decision_response(data)
        assert result.action == "answer_directly"

    def test_missing_fields_use_defaults(self):
        """Test that missing fields use sensible defaults."""
        data = {}
        result = validate_decision_response(data)
        assert result.action == "answer_directly"
        assert result.reasoning == ""
        assert result.requires_planning is False
        assert result.target_capabilities == []
        assert result.direct_response is None

    def test_null_values_handled(self):
        """Test that null values are handled gracefully."""
        data = {
            "action": None,
            "reasoning": None,
            "target_capabilities": None,
        }
        result = validate_decision_response(data)
        assert result.action == "answer_directly"
        assert result.reasoning == ""
        assert result.target_capabilities == []

    def test_capabilities_as_string(self):
        """Test that single capability string becomes list."""
        data = {"target_capabilities": "search"}
        result = validate_decision_response(data)
        assert result.target_capabilities == ["search"]

    def test_valid_build_action(self):
        """Test valid build action."""
        data = {
            "action": "build_agent",
            "reasoning": "User wants a new agent",
            "requires_planning": True,
            "target_capabilities": ["code_generation", "testing"],
        }
        result = validate_decision_response(data)
        assert result.action == "build_agent"
        assert result.requires_planning is True


class TestPlannerResponse:
    """Tests for PlannerResponse validation."""

    def test_valid_planner_response(self):
        """Test parsing valid planner response."""
        data = {
            "work_items": [
                {
                    "id": "item_1",
                    "type": "build_agent",
                    "description": "Build a test agent",
                    "priority": 1,
                }
            ],
            "summary": "Building a test agent"
        }
        result = validate_planner_response(data)
        assert len(result.work_items) == 1
        assert result.work_items[0].item_type == "build_agent"

    def test_empty_work_items(self):
        """Test handling of empty work items."""
        data = {"work_items": []}
        result = validate_planner_response(data)
        assert result.work_items == []

    def test_null_work_items(self):
        """Test handling of null work items."""
        data = {"work_items": None}
        result = validate_planner_response(data)
        assert result.work_items == []

    def test_single_item_dict(self):
        """Test that single dict becomes list."""
        data = {
            "work_items": {
                "id": "item_1",
                "type": "a2a_call",
                "description": "Call agent"
            }
        }
        result = validate_planner_response(data)
        assert len(result.work_items) == 1

    def test_item_type_normalization(self):
        """Test work item type normalization."""
        data = {
            "work_items": [
                {"type": "build", "description": "test"},
                {"type": "mcp", "description": "test"},
                {"type": "tool", "description": "test"},
            ]
        }
        result = validate_planner_response(data)
        assert result.work_items[0].item_type == "build_agent"
        assert result.work_items[1].item_type == "build_mcp"
        assert result.work_items[2].item_type == "mcp_call"


class TestBuildSpec:
    """Tests for BuildSpec validation."""

    def test_valid_build_spec(self):
        """Test parsing valid build spec."""
        data = {
            "name": "my-agent",
            "description": "A helpful agent",
            "kind": "agent",
            "capabilities": ["search", "code_generation"],
            "entrypoint": "agent.py",
            "dependencies": ["openai", "langchain"],
            "config": {"model": "gpt-4"}
        }
        result = validate_build_spec(data)
        assert result.name == "my-agent"
        assert result.kind == "agent"
        assert len(result.capabilities) == 2

    def test_name_sanitization(self):
        """Test that invalid characters are removed from name."""
        data = {"name": "my<agent>:test?"}
        result = validate_build_spec(data)
        assert "<" not in result.name
        assert ">" not in result.name
        assert ":" not in result.name
        assert "?" not in result.name

    def test_empty_name_default(self):
        """Test that empty name gets default."""
        data = {"name": ""}
        result = validate_build_spec(data)
        assert result.name == "unnamed"

    def test_kind_normalization(self):
        """Test build kind normalization."""
        data = {"kind": "build_agent"}
        result = validate_build_spec(data)
        assert result.kind == "agent"

        data = {"kind": "mcp_server"}
        result = validate_build_spec(data)
        assert result.kind == "mcp"

    def test_missing_fields_defaults(self):
        """Test defaults for missing fields."""
        data = {}
        result = validate_build_spec(data)
        assert result.name == "unnamed"
        assert result.description == ""
        assert result.kind == "agent"
        assert result.entrypoint == "main.py"
        assert result.dependencies == []


class TestVerificationResult:
    """Tests for VerificationResult validation."""

    def test_valid_verification(self):
        """Test parsing valid verification result."""
        data = {
            "passed": True,
            "score": 0.85,
            "criteria_scores": {"test_coverage": 0.9, "code_quality": 0.8},
            "gaps": [],
            "next_actions": [],
            "evidence": "All tests pass"
        }
        result = validate_verification_result(data)
        assert result.passed is True
        assert result.score == 0.85

    def test_score_clamping(self):
        """Test that scores are clamped to 0-1 range."""
        data = {"score": 1.5}
        result = validate_verification_result(data)
        assert result.score == 1.0

        data = {"score": -0.5}
        result = validate_verification_result(data)
        assert result.score == 0.0

    def test_null_score(self):
        """Test null score becomes 0."""
        data = {"score": None}
        result = validate_verification_result(data)
        assert result.score == 0.0

    def test_invalid_score_type(self):
        """Test invalid score type becomes 0."""
        data = {"score": "high"}
        result = validate_verification_result(data)
        assert result.score == 0.0

    def test_defaults(self):
        """Test default values."""
        data = {}
        result = validate_verification_result(data)
        assert result.passed is False
        assert result.score == 0.0
        assert result.gaps == []
        assert result.next_actions == []


class TestWorkItemSpec:
    """Tests for WorkItemSpec validation."""

    def test_type_alias(self):
        """Test that 'type' alias works for 'item_type'."""
        data = {
            "id": "item_1",
            "type": "build_agent",
            "description": "Build test agent"
        }
        result = WorkItemSpec.model_validate(data)
        assert result.item_type == "build_agent"

    def test_empty_type_default(self):
        """Test empty type defaults to direct_answer."""
        data = {"type": ""}
        result = WorkItemSpec.model_validate(data)
        assert result.item_type == "direct_answer"

    def test_context_default(self):
        """Test context defaults to empty dict."""
        data = {}
        result = WorkItemSpec.model_validate(data)
        assert result.context == {}


class TestEdgeCases:
    """Test edge cases and malformed input."""

    def test_completely_empty_dict(self):
        """Test handling of completely empty dict."""
        result = validate_decision_response({})
        assert result.action == "answer_directly"

        result = validate_planner_response({})
        assert result.work_items == []

        result = validate_build_spec({})
        assert result.name == "unnamed"

    def test_extra_fields_ignored(self):
        """Test that extra fields are ignored."""
        data = {
            "action": "answer_directly",
            "unknown_field": "some_value",
            "another_unknown": 123,
        }
        result = validate_decision_response(data)
        assert result.action == "answer_directly"
        # Extra fields should not cause errors

    def test_nested_invalid_data(self):
        """Test handling of nested invalid data."""
        data = {
            "work_items": [
                {"type": None, "description": None},
                {"type": 123, "description": ["not", "a", "string"]},
            ]
        }
        result = validate_planner_response(data)
        # Should not raise, should use defaults
        assert len(result.work_items) == 2
