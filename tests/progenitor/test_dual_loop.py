"""
Basic tests for loop.spec.md dual-loop implementation.

Tests the core components: IdealState, Verifier, and phase nodes.
"""

import pytest

from app.progenitor.state.ideal_state import (
    AntiCriterion,
    CriterionType,
    IdealState,
    IdealStateBuilder,
    StopCondition,
    SuccessCriterion,
)
from app.progenitor.state.verifier import VerificationReport, Verifier


class TestIdealState:
    """Test ideal state models and builder."""

    def test_ideal_state_creation(self):
        """Test creating an ideal state."""
        ideal_state = IdealState(
            task_description="Test task",
            success_criteria=[
                SuccessCriterion(
                    id="test_1",
                    description="Test passes",
                    criterion_type=CriterionType.FUNCTIONAL,
                    rank=1,
                    measurement="Run tests",
                    target_score=0.8,
                )
            ],
            stop_conditions=[
                StopCondition(condition_type="min_score", threshold=0.8),
                StopCondition(condition_type="max_iterations", threshold=5),
            ],
        )

        assert ideal_state.task_description == "Test task"
        assert len(ideal_state.success_criteria) == 1
        assert len(ideal_state.stop_conditions) == 2
        assert ideal_state.version == 1

    def test_ideal_state_refinement(self):
        """Test refining an ideal state."""
        ideal_state = IdealState(
            task_description="Test task",
            success_criteria=[],
        )

        # Add criterion
        ideal_state.add_success_criterion(
            SuccessCriterion(
                id="new_criterion",
                description="New requirement",
                criterion_type=CriterionType.QUALITY,
                rank=1,
                measurement="Check quality",
                target_score=0.9,
            )
        )

        assert len(ideal_state.success_criteria) == 1

        # Refine
        refined = ideal_state.refine({"task_description": "Updated task"})
        assert refined.version == 2
        assert refined.task_description == "Updated task"

    def test_stop_conditions(self):
        """Test stop condition checking."""
        ideal_state = IdealState(
            task_description="Test",
            stop_conditions=[
                StopCondition(condition_type="min_score", threshold=0.8),
                StopCondition(condition_type="max_iterations", threshold=3),
            ],
        )

        # Should not stop at iteration 1 with low score
        # CQ-008: check_stop_conditions now returns 3 values (immutable pattern)
        should_stop, reasons, met_ids = ideal_state.check_stop_conditions(
            current_score=0.5,
            iteration=1,
            elapsed_time=10,
            tool_calls=5,
        )
        assert not should_stop

        # Should stop when score threshold met
        should_stop, reasons, met_ids = ideal_state.check_stop_conditions(
            current_score=0.85,
            iteration=1,
            elapsed_time=10,
            tool_calls=5,
        )
        assert should_stop
        assert "score" in reasons[0].lower()

        # Should stop at max iterations
        should_stop, reasons, met_ids = ideal_state.check_stop_conditions(
            current_score=0.5,
            iteration=3,
            elapsed_time=10,
            tool_calls=5,
        )
        assert should_stop
        assert "iteration" in reasons[0].lower()


class TestVerifier:
    """Test verification system."""

    @pytest.mark.asyncio
    async def test_basic_verification(self):
        """Test basic verification without LLM."""
        verifier = Verifier()

        ideal_state = IdealState(
            task_description="Test task",
            success_criteria=[
                SuccessCriterion(
                    id="output_exists",
                    description="Output exists",
                    criterion_type=CriterionType.FUNCTIONAL,
                    rank=1,
                    measurement="Check output is not empty",
                    target_score=0.5,
                )
            ],
        )

        output = "Some test output"
        context = {"completed": True}

        report = await verifier.verify(
            ideal_state=ideal_state,
            output=output,
            iteration=1,
            context=context,
        )

        assert isinstance(report, VerificationReport)
        assert report.iteration == 1
        assert len(report.criterion_scores) == 1
        assert report.overall_score > 0

    @pytest.mark.asyncio
    async def test_anti_criterion_violations(self):
        """Test anti-criterion checking."""
        verifier = Verifier()

        ideal_state = IdealState(
            task_description="Test",
            success_criteria=[
                SuccessCriterion(
                    id="works",
                    description="Works",
                    criterion_type=CriterionType.FUNCTIONAL,
                    rank=1,
                    measurement="Heuristic",
                    target_score=0.5,
                )
            ],
            anti_criteria=[
                AntiCriterion(
                    id="no_verbose",
                    description="Avoid verbose output",
                    severity=1.0,
                    detection="Check length",
                )
            ],
        )

        # Very long output should trigger violation
        output = "x" * 6000
        context = {}

        report = await verifier.verify(
            ideal_state=ideal_state,
            output=output,
            iteration=1,
            context=context,
        )

        assert len(report.anti_criterion_violations) > 0
        assert report.overall_score < 1.0  # Penalty applied

    def test_verification_report_summary(self):
        """Test compact verification summary."""
        report = VerificationReport(
            iteration=1,
            timestamp="2025-01-20T12:00:00Z",
            criterion_scores=[],
            overall_score=0.85,
            passed=True,
            gaps=[],
            next_actions=[],
            summary="Test passed",
        )

        summary = report.to_compact_summary()
        assert "0.85" in summary
        assert "PASS" in summary


class TestIdealStateBuilder:
    """Test ideal state builder."""

    @pytest.mark.asyncio
    async def test_build_default_ideal_state(self):
        """Test building a default ideal state without LLM."""
        builder = IdealStateBuilder(llm_client=None)

        ideal_state = builder._create_default_ideal_state("Build a Python script")

        assert ideal_state.task_description == "Build a Python script"
        assert len(ideal_state.success_criteria) > 0
        assert len(ideal_state.stop_conditions) > 0


class TestArtefactTracking:
    """Test artefact tracking per loop.spec.md."""

    def test_state_has_artefact_fields(self):
        """Test that ProgenitorState has artefact tracking fields."""
        import typing

        from app.progenitor.state.models import (
            ProgenitorState,
        )

        # Check TypedDict has required fields (now nested)
        hints = typing.get_type_hints(ProgenitorState)
        assert "history" in hints  # Contains artefacts, verification_reports
        assert "best" in hints  # Contains iteration, score, passed
        assert "task_id" in hints
        assert "phases" in hints  # Contains verify with score, gaps

    def test_run_id_generation(self):
        """Test run_id generation utility."""
        from app.progenitor.state.models import generate_run_id

        run_id = generate_run_id()
        assert run_id.startswith("run_")
        assert len(run_id) == 16  # "run_" + 12 hex chars

        # Test uniqueness
        run_id2 = generate_run_id()
        assert run_id != run_id2


class TestVerificationSummary:
    """Test verification summary in response per loop.spec.md."""

    def test_compact_verification_summary_format(self):
        """Test that verification report produces compact summary."""
        report = VerificationReport(
            iteration=3,
            timestamp="2026-01-20T12:00:00Z",
            criterion_scores=[],
            anti_criterion_violations=[],
            overall_score=0.75,
            passed=False,
            gaps=["Missing feature X", "Performance below target"],
            next_actions=["Add feature X"],
            summary="Partial success",
        )

        summary = report.to_compact_summary()
        assert "0.75" in summary
        assert "FAIL" in summary or "partial" in summary.lower()


class TestPlanExperiment:
    """Test PLAN phase experiment generation per loop.spec.md."""

    def test_experiment_plan_schema(self):
        """Test ExperimentPlan schema structure."""
        from app.progenitor.state.execution_schemas import ExperimentPlan, PromptSpec, ScoringHook

        # Create an ExperimentPlan
        plan = ExperimentPlan(
            plan_id="test-plan-1",
            experiment_description="Test caching behavior",
            agent_name="test-agent",
            iteration=1,
            prompts=[
                PromptSpec(
                    id="p1",
                    text="Hello",
                    expected_response_contains=["success"],
                    is_smoke_test=True,
                ),
            ],
            scoring_hooks=[
                ScoringHook(
                    id="h1",
                    name="check_success",
                    hook_type="contains",
                    pattern="success",
                ),
            ],
            num_trials=3,
        )

        assert plan.plan_id == "test-plan-1"
        assert plan.agent_name == "test-agent"
        assert len(plan.prompts) == 1
        assert plan.prompts[0].is_smoke_test is True
        assert len(plan.scoring_hooks) == 1
        assert plan.num_trials == 3

    def test_spec_doc_schema(self):
        """Test SpecDoc schema for persistent spec artifact."""
        from app.progenitor.state.execution_schemas import SpecDoc

        # Create a SpecDoc from seed
        spec = SpecDoc.create_from_seed(
            seed_content="# Agent Spec\n\nBuild a hello world agent.",
            seed_path="prompts/agent_structure.md",
            agent_name="hello_agent",
        )

        assert spec.revision == 1
        assert "hello world" in spec.content.lower()
        assert spec.source_seed_path == "prompts/agent_structure.md"
        assert spec.agent_name == "hello_agent"
        assert len(spec.history) == 1
        assert spec.history[0].summary.startswith("Seeded from")

        # Update from THINK outputs
        approaches = [
            {"name": "approach_1", "description": "Use simple HTTP handler"},
            {"name": "approach_2", "description": "Use async patterns"},
        ]
        gaps = ["Response time too slow"]

        spec.update_from_think(
            think_outputs=approaches,
            iteration=1,
            verification_gaps=gaps,
        )

        assert spec.revision == 2
        assert spec.iteration_last_updated == 1
        assert "approach_1" in spec.content.lower() or "http handler" in spec.content.lower()
        assert "gaps to address" in spec.content.lower()
        assert len(spec.history) == 2

    def test_spec_doc_copilot_prompt_field(self):
        """Test SpecDoc has copilot_prompt field for referencing spec file."""
        from app.progenitor.state.execution_schemas import SpecDoc

        # Create a SpecDoc and set copilot_prompt
        spec = SpecDoc.create_from_seed(
            seed_content="# Agent Spec\n\nBuild a hello world agent.",
            seed_path="prompts/agent_structure.md",
            agent_name="hello_agent",
        )

        # copilot_prompt should be None initially (set by PLAN phase)
        assert spec.copilot_prompt is None

        # Set the copilot_prompt (as PLAN would do)
        spec.copilot_prompt = "Build based on spec at build/agents/hello_agent_spec.md"
        spec.spec_file_path = "build/agents/hello_agent_spec.md"

        assert "Build based on spec" in spec.copilot_prompt
        assert spec.spec_file_path == "build/agents/hello_agent_spec.md"

        # Verify it serializes correctly
        spec_dict = spec.model_dump()
        assert "copilot_prompt" in spec_dict
        assert spec_dict["copilot_prompt"] == spec.copilot_prompt

    def test_select_best_approach_with_gaps(self):
        """Test approach selection considers gaps."""
        from app.progenitor.nodes.planner import _select_best_approach

        approaches = [
            {"name": "approach_1", "description": "Improve UI styling"},
            {"name": "approach_2", "description": "Fix latency and performance issues"},
        ]

        # Verification with performance gap should select approach_2
        last_verification = {
            "gaps": ["Performance below target", "High latency"]
        }

        selected = _select_best_approach(approaches, last_verification)
        assert selected["name"] == "approach_2"


class TestExecutePhase:
    """Test EXECUTE phase per loop.spec.md BUILD → EXECUTE → VERIFY flow."""

    @pytest.mark.asyncio
    async def test_execute_node_no_tools(self):
        """Test execute node when no tools are pending."""
        from app.progenitor.nodes import execute
        from app.progenitor.state.models import (
            ProgenitorState,
            WorkItem,
            WorkItemStatus,
            WorkItemType,
        )

        # Mock tool registry
        class MockRegistry:
            def get_tool(self, name):
                return None

        node_fn = execute.create_node(MockRegistry())

        state: ProgenitorState = {
            "current_work_item": WorkItem(
                id="test",
                item_type=WorkItemType.DIRECT_ANSWER,
                status=WorkItemStatus.PENDING,
                description="Test",
            ),
            "phases": {"execute": {"result": {"output": "some output"}}},
            "work_item_completed": True,
        }

        result = await node_fn(state)

        phases = result.get("phases") or {}
        execute_phase = phases.get("execute") or {}
        assert execute_phase.get("completed") is True
        assert execute_phase.get("result") is not None

    @pytest.mark.asyncio
    async def test_execute_node_with_pending_tools(self):
        """Test execute node with pending tool calls."""
        from app.progenitor.nodes import execute
        from app.progenitor.state.models import (
            ProgenitorState,
            WorkItem,
            WorkItemStatus,
            WorkItemType,
        )

        # Mock tool registry
        class MockRegistry:
            def get_tool(self, name):
                return None  # Tool not found

        node_fn = execute.create_node(MockRegistry())

        state: ProgenitorState = {
            "current_work_item": WorkItem(
                id="test",
                item_type=WorkItemType.SYSTEM_TOOL,
                status=WorkItemStatus.IN_PROGRESS,
                description="Test",
            ),
            "phases": {"execute": {"result": {"output": "build output"}}},
            "work_item_completed": False,
            "pending_tool_calls": [
                {"tool": "unknown_tool", "arguments": {}},
            ],
        }

        result = await node_fn(state)

        phases = result.get("phases") or {}
        execute_phase = phases.get("execute") or {}
        assert execute_phase.get("completed") is True
        # Should report error for unknown tool - check in result
        exec_result = execute_phase.get("result") or {}
        build_output = exec_result.get("build_output", {})
        assert build_output.get("all_succeeded") is False


class TestIdealStateRefinement:
    """Test ideal state refinement with anti-criteria from violations."""

    @pytest.mark.asyncio
    async def test_extract_anti_criteria_from_violations(self):
        """Test that violations become anti-criteria via LLM structured output."""
        import json
        from unittest.mock import AsyncMock

        from app.progenitor.state.ideal_state import IdealStateBuilder

        class _MockLLM:
            def __init__(self, rv):
                self.chat = AsyncMock(return_value=rv)

        mock_llm = _MockLLM(json.dumps({
            "anti_criteria": [
                {"id": "anti_violation_1", "description": "Avoid verbose output",
                 "severity": 0.8, "detection": "Check word count"},
                {"id": "anti_violation_2", "description": "Avoid exceeding response time limit",
                 "severity": 1.0, "detection": "Check response latency"},
            ],
            "reasoning": "Violations indicate verbosity and timing issues",
        }))

        builder = IdealStateBuilder(mock_llm)

        violations = [
            {"description": "Output was too verbose", "severity": 0.8},
            {"description": "Response time exceeded limit", "severity": 1.0},
        ]

        ideal_state = IdealState(
            task_description="Test task",
            anti_criteria=[],
        )

        new_anti = await builder.extract_anti_criteria_from_violations(violations, ideal_state)

        assert len(new_anti) == 2
        assert "verbose" in new_anti[0].description.lower()
        assert "response time" in new_anti[1].description.lower() or "limit" in new_anti[1].description.lower()

    def test_ideal_state_add_anti_criterion(self):
        """Test adding anti-criteria to ideal state."""
        ideal_state = IdealState(
            task_description="Test",
            anti_criteria=[],
        )

        ideal_state.add_anti_criterion(AntiCriterion(
            id="anti_test",
            description="Avoid fluff",
            severity=1.0,
            detection="Check for redundancy",
        ))

        assert len(ideal_state.anti_criteria) == 1
        assert ideal_state.anti_criteria[0].id == "anti_test"


class TestLoopSpecConformance:
    """Tests ensuring implementation matches loop.spec.md exactly."""

    def test_inner_loop_phases_exist(self):
        """Test all inner loop phases are implemented."""
        from app.progenitor.nodes import (
            execute,
            learn,
            observe,
            planner,  # PLAN phase
            think,
            verify_loop,
        )

        # All phases should have create_node function
        assert hasattr(observe, "create_node")
        assert hasattr(think, "create_node")
        assert hasattr(planner, "create_node")  # PLAN
        assert hasattr(execute, "create_node")  # EXECUTE
        assert hasattr(verify_loop, "create_node")  # VERIFY
        assert hasattr(learn, "create_node")

    def test_graph_has_execute_node(self):
        """Test that outer loop graph imports correctly."""
        from app.progenitor.graph_chat_loop import create_progenitor_graph
        # Just verify the import works - full graph creation needs mocks
        assert callable(create_progenitor_graph)

    def test_verification_report_has_required_fields(self):
        """Test VerificationReport matches loop.spec.md contract."""
        # Per spec: "Every iteration produces:
        # - Score per criterion
        # - Pass/fail summary
        # - Evidence or heuristic notes
        # - Gaps and next actions"

        report = VerificationReport(
            iteration=1,
            timestamp="2026-01-20T12:00:00Z",
            criterion_scores=[],  # Score per criterion
            overall_score=0.8,
            passed=True,  # Pass/fail summary
            gaps=["Gap 1"],  # Gaps
            next_actions=["Action 1"],  # Next actions
            summary="Test",  # Evidence/notes
        )

        assert hasattr(report, "criterion_scores")
        assert hasattr(report, "passed")
        assert hasattr(report, "gaps")
        assert hasattr(report, "next_actions")

    def test_artefact_tracking_fields(self):
        """Test Task State has artefact tracking per loop.spec.md."""
        # Per spec Task State: "Artefacts, Verification reports, Lessons learned"
        from app.progenitor.state.models import ProgenitorState

        # ProgenitorState is a TypedDict, check it allows these fields (nested)
        state: ProgenitorState = {
            "history": {
                "artefacts": [],
                "verification_reports": [],
                "lessons_learned": [],
            },
            "best": {
                "iteration": 0,
                "score": 0.0,
                "passed": False,
            },
        }

        assert "history" in state
        assert "artefacts" in state["history"]
        assert "lessons_learned" in state["history"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
