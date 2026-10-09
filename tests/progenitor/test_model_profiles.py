import pytest

from app.progenitor.state.model_profiles import (
    default_model_profiles_payload,
    validate_model_profiles_document,
)


def test_default_profile_is_the_azure_general_provider(monkeypatch):
    monkeypatch.setenv("PROGENITOR_MODEL_DEFAULT", "deployment-general")

    assert default_model_profiles_payload() == {
        "general": {"provider": "azure_openai", "deployment": "deployment-general"}
    }


def test_profile_document_requires_an_azure_provider_and_deployment():
    profile = validate_model_profiles_document(
        '{"general": {"provider": "azure_openai", "deployment": "deployment-general"}}'
    )

    assert profile["general"]["deployment"] == "deployment-general"
    with pytest.raises(ValueError, match="provider"):
        validate_model_profiles_document('{"general": {"provider": "openai", "deployment": "x"}}')
