import pytest

from app.progenitor.state.model_profiles import (
    default_model_profiles_payload,
    migrate_legacy_model_profiles_payload,
    validate_model_profiles_document,
    validate_model_profiles_payload,
)


def test_default_profiles_do_not_fabricate_a_provider():
    assert default_model_profiles_payload() == {}


def test_profile_document_requires_persisted_provider_and_deployment():
    profile = validate_model_profiles_document(
        '{"general": {"provider_id": "foundry-1", "deployment": "deployment-general"}}'
    )

    assert profile["general"] == {
        "provider_id": "foundry-1",
        "deployment": "deployment-general",
    }
    with pytest.raises(ValueError, match="provider_id"):
        validate_model_profiles_document('{"general": {"deployment": "x"}}')


def test_legacy_azure_profile_is_converted_once_to_foundry_provider_id():
    migrated = migrate_legacy_model_profiles_payload(
        {"general": {"provider": "azure_openai", "deployment": "deployment-general"}},
        "foundry-1",
    )

    assert migrated == {"general": {"provider_id": "foundry-1", "deployment": "deployment-general"}}
    assert migrate_legacy_model_profiles_payload(migrated, "foundry-1") == migrated


def test_legacy_migration_discards_connection_fields_but_keeps_model_behaviour():
    migrated = migrate_legacy_model_profiles_payload(
        {
            "general": {
                "provider": "azure_openai",
                "deployment": "deployment-general",
                "api_key": "previously-valid-secret",
                "api_base": "https://old-resource.openai.azure.com",
                "api_version": "2024-10-21",
                "reasoning_enabled": True,
                "reasoning_effort": "high",
                "reasoning_summary": "concise",
            }
        },
        "foundry-1",
    )

    assert migrated == {
        "general": {
            "provider_id": "foundry-1",
            "deployment": "deployment-general",
            "reasoning_enabled": True,
            "reasoning_effort": "high",
            "reasoning_summary": "concise",
        }
    }


def test_pricing_accepts_zero_rates_and_normalizes_null_to_unpriced():
    profiles = validate_model_profiles_payload(
        {
            "free": {
                "provider_id": "foundry-1",
                "deployment": "zero-rate",
                "pricing": {
                    "currency": "USD",
                    "input_per_million": "0",
                    "cached_input_per_million": "0.00",
                    "output_per_million": "0.000",
                },
            },
            "unpriced": {
                "provider_id": "foundry-1",
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
                    "provider_id": "foundry-1",
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
