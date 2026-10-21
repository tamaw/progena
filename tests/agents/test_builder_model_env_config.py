from app.agents.builder_agent.config import CopilotConfig as BuilderAgentCopilotConfig
from app.agents.builder_agent.config import LLMConfig as BuilderAgentLLMConfig
from app.agents.builder_progenitor.config import CopilotConfig as BuilderProgenitorCopilotConfig
from app.agents.builder_progenitor.config import LLMConfig as BuilderProgenitorLLMConfig


def test_builder_agent_uses_dedicated_model_env_vars(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_MODEL_DEFAULT", "shared-default")
    monkeypatch.setenv("OVERRIDE_AGENT_PLANNER_MODEL", "shared-planner")
    monkeypatch.setenv("OVERRIDE_AGENT_BUILDER_MODEL", "shared-builder")
    monkeypatch.setenv("DEFAULT_COPILOT_MODEL", "agent-copilot")

    llm = BuilderAgentLLMConfig.from_env()
    copilot = BuilderAgentCopilotConfig.from_env()

    assert llm.model_default == "shared-default"
    assert llm.model_planner == "shared-planner"
    assert llm.model_builder == "shared-builder"
    assert copilot.model == "agent-copilot"


def test_builder_progenitor_uses_dedicated_model_env_vars(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_MODEL_DEFAULT", "shared-default")
    monkeypatch.setenv("DEFAULT_COPILOT_MODEL", "gpt-5.4")

    llm = BuilderProgenitorLLMConfig.from_env()
    copilot = BuilderProgenitorCopilotConfig.from_env()

    assert llm.model_default == "shared-default"
    assert llm.get_model() == "shared-default"
    assert copilot.model == "gpt-5.4"
