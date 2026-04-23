import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

os.environ.setdefault("PROGENITOR_DATA_DIR", str(Path(__file__).resolve().parents[3] / "data"))

from app.agents.builder_agent.llm.prompts import PromptTemplates
from app.agents.builder_agent.nodes.learn import _generate_lessons_via_llm
from app.agents.builder_agent.runtime.decide import _request_decision_data


@pytest.mark.asyncio
async def test_request_decision_data_raises_on_invalid_json():
    llm_client = SimpleNamespace(
        _get_json_response_format=MagicMock(return_value={"type": "json_schema"}),
        chat=AsyncMock(return_value="not-json"),
    )

    with pytest.raises(ValueError, match="structured decision response"):
        await _request_decision_data(
            llm_client=llm_client,
            user_input="build an agent",
            conversation_history=[],
            tool_context="",
        )


@pytest.mark.asyncio
async def test_generate_lessons_surfaces_parse_failure():
    llm_client = SimpleNamespace(
        _get_json_response_format=MagicMock(return_value={"type": "json_schema"}),
        chat=AsyncMock(return_value="not-json"),
    )

    result = await _generate_lessons_via_llm(
        llm_client=llm_client,
        context={"iteration": 1, "user_request": "improve the build"},
        prompts=PromptTemplates(),
    )

    assert result.plan_achieved is False
    assert "Invalid learning JSON" in result.plan_achievement_reasoning
    assert any("Invalid learning JSON" in failure for failure in result.what_failed)
