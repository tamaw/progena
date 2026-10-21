import pytest

from app.progenitor.state.model_profiles import (
    default_model_profiles_payload,
    validate_model_profiles_document,
    validate_model_profiles_payload,
)


def test_default_profile_is_the_azure_general_provider(monkeypatch):
    monkeypatch.setenv("PROGENITOR_MODEL_DEFAULT", "deployment-general")
    monkeypatch.delenv("PROGENITOR_MODEL_DEFAULT_MODEL", raising=False)
    monkeypatch.delenv("PROGENITOR_MODEL_DEFAULT_PRICING_TIER", raising=False)

    assert default_model_profiles_payload() == {
        "general": {"provider": "azure_openai", "deployment": "deployment-general"}
    }


def test_default_profile_has_pricing_only_for_explicit_supported_model_mapping(monkeypatch):
    monkeypatch.setenv("PROGENITOR_MODEL_DEFAULT", "azure-deployment-name")
    monkeypatch.setenv("PROGENITOR_MODEL_DEFAULT_MODEL", "gpt-5-mini")
    monkeypatch.setenv("PROGENITOR_MODEL_DEFAULT_PRICING_TIER", "global_standard")

    assert default_model_profiles_payload()["general"] == {
        "provider": "azure_openai",
        "deployment": "azure-deployment-name",
        "pricing": {
            "currency": "USD",
            "input_per_million": "0.25",
            "cached_input_per_million": "0.025",
            "output_per_million": "2.00",
        },
    }


def test_default_pricing_requires_an_explicit_supported_tier(monkeypatch):
    monkeypatch.setenv("PROGENITOR_MODEL_DEFAULT", "azure-deployment-name")
    monkeypatch.setenv("PROGENITOR_MODEL_DEFAULT_MODEL", "gpt-5-mini")
    monkeypatch.delenv("PROGENITOR_MODEL_DEFAULT_PRICING_TIER", raising=False)

    assert "pricing" not in default_model_profiles_payload()["general"]


def test_unknown_default_deployment_is_unpriced(monkeypatch):
    monkeypatch.setenv("PROGENITOR_MODEL_DEFAULT", "unknown-azure-deployment")
    monkeypatch.delenv("PROGENITOR_MODEL_DEFAULT_MODEL", raising=False)
    monkeypatch.delenv("PROGENITOR_MODEL_DEFAULT_PRICING_TIER", raising=False)

    assert "pricing" not in default_model_profiles_payload()["general"]


def test_profile_document_requires_an_azure_provider_and_deployment():
    profile = validate_model_profiles_document(
        '{"general": {"provider": "azure_openai", "deployment": "deployment-general"}}'
    )

    assert profile["general"]["deployment"] == "deployment-general"
    with pytest.raises(ValueError, match="provider"):
        validate_model_profiles_document('{"general": {"provider": "openai", "deployment": "x"}}')


def test_pricing_accepts_zero_rates_and_normalizes_null_to_unpriced():
    profiles = validate_model_profiles_payload(
        {
            "free": {
                "provider": "azure_openai",
                "deployment": "zero-rate",
                "pricing": {
                    "currency": "USD",
                    "input_per_million": "0",
                    "cached_input_per_million": "0.00",
                    "output_per_million": "0.000",
                },
            },
            "unpriced": {
                "provider": "azure_openai",
                "deployment": "no-rate",
                "pricing": None,
            },
        }
    )

    assert profiles["free"]["pricing"]["output_per_million"] == "0.000"
    assert "pricing" not in profiles["unpriced"]


@pytest.mark.parametrize("rate", ["-0.01", "NaN", "Infinity", "-Infinity", "1.0000000000001"])
def test_pricing_rejects_nonfinite_negative_and_excess_precision_rates(rate):
    with pytest.raises(ValueError, match="pricing.input_per_million"):
        validate_model_profiles_payload(
            {
                "general": {
                    "provider": "azure_openai",
                    "deployment": "deployment",
                    "pricing": {
                        "currency": "USD",
                        "input_per_million": rate,
                        "cached_input_per_million": "0",
                        "output_per_million": "1",
                    },
                }
            }
        )
