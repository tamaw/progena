"""Tests for builder_agent Azure structured schema formatting."""

from app.agents.builder_agent.llm.client import _build_json_schema_format
from app.agents.builder_agent.nodes.think import ThinkResponse
from app.agents.builder_agent.state.ideal_state import IdealState


def test_schema_format_closes_nested_object_defs():
    response_format = _build_json_schema_format(ThinkResponse, "think_response")
    schema = response_format["json_schema"]["schema"]

    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {
        "approaches",
        "recommended_approach_index",
        "recommendation_reasoning",
        "reasoning_trace",
    }
    assert schema["$defs"]["ThinkApproachResponse"]["additionalProperties"] is False
    assert set(schema["$defs"]["ThinkApproachResponse"]["required"]) == {
        "name",
        "strategy",
        "implementation_steps",
        "trade_offs",
        "risks",
        "expected_success_probability",
        "addresses_gaps",
    }
    assert schema["$defs"]["ThinkTradeOffResponse"]["additionalProperties"] is False
    assert schema["$defs"]["ThinkRiskResponse"]["additionalProperties"] is False


def test_schema_format_strips_ref_siblings_for_azure():
    response_format = _build_json_schema_format(IdealState, "ideal_state")
    schema = response_format["json_schema"]["schema"]
    criterion_type = schema["$defs"]["SuccessCriterion"]["properties"]["criterion_type"]

    assert criterion_type == {"$ref": "#/$defs/CriterionType"}
