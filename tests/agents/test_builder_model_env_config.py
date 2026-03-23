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


def test_builder_agent_falls_back_to_shared_model_env_vars(monkeypatch) -> None:
    llm = BuilderAgentLLMConfig.from_env()
    copilot = BuilderAgentCopilotConfig.from_env()

    assert llm.model_default == "gpt-5-mini"
    assert llm.model_planner is None
    assert llm.model_builder is None
    assert copilot.model == "gpt-5-mini"


def test_builder_progenitor_uses_dedicated_model_env_vars(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_MODEL_DEFAULT", "shared-default")
    monkeypatch.setenv("DEFAULT_COPILOT_MODEL", "gpt-5.4")

    llm = BuilderProgenitorLLMConfig.from_env()
    copilot = BuilderProgenitorCopilotConfig.from_env()

    assert llm.model_default == "shared-default"
    assert llm.get_model() == "shared-default"
    assert copilot.model == "gpt-5.4"


def test_builder_progenitor_defaults_without_role_specific_models(monkeypatch) -> None:
    llm = BuilderProgenitorLLMConfig.from_env()
    copilot = BuilderProgenitorCopilotConfig.from_env()

    assert llm.model_default == "gpt-5-mini"
    assert llm.get_model() == "gpt-5-mini"
    assert copilot.model == "gpt-5-mini"
