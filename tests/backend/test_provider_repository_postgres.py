"""Real-Postgres concurrency coverage for persisted provider settings.

Opt-in on a scratch database (``PROGENITOR_TEST_DATABASE_URL``).
"""

from __future__ import annotations

import os
import threading

import psycopg
import pytest
from cryptography.fernet import Fernet

from app.progenitor.settings.providers import ProviderRepository

DATABASE_URL = (os.getenv("PROGENITOR_TEST_DATABASE_URL") or "").strip() or None

pytestmark = pytest.mark.skipif(
    DATABASE_URL is None, reason="requires PROGENITOR_TEST_DATABASE_URL (a scratch database)"
)


@pytest.fixture
def repositories(monkeypatch):
    assert DATABASE_URL is not None
    monkeypatch.delenv("PROGENITOR_DATABASE_URL", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_BASE_URL", raising=False)
    monkeypatch.setenv("PROGENITOR_PROVIDER_ENCRYPTION_KEY", Fernet.generate_key().decode("ascii"))
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        conn.execute("DROP TABLE IF EXISTS provider_connection")
        conn.execute("DROP TABLE IF EXISTS provider_repository_metadata")
    first = ProviderRepository(DATABASE_URL)
    second = ProviderRepository(DATABASE_URL)
    yield first, second
    first.close()
    second.close()
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        conn.execute("DROP TABLE IF EXISTS provider_connection")
        conn.execute("DROP TABLE IF EXISTS provider_repository_metadata")


@pytest.mark.parametrize(
    "legacy_endpoint",
    [
        "https://sample.openai.azure.com/openai/v1/responses?api-version=preview",
        "https://sample.openai.azure.com/openai/responses?api-version=preview",
        "https://sample.openai.azure.com/openai?api-version=preview",
    ],
)
def test_concurrent_schema_initialization_seeds_legacy_endpoint_once(
    repositories, monkeypatch, legacy_endpoint
):
    first, second = repositories
    monkeypatch.setenv("AZURE_OPENAI_BASE_URL", legacy_endpoint)
    ready = threading.Barrier(3)
    failures: list[Exception] = []

    def open_repository(repository: ProviderRepository) -> None:
        try:
            ready.wait()
            repository.open()
        except Exception as exc:  # asserted below after both workers finish
            failures.append(exc)

    workers = [
        threading.Thread(target=open_repository, args=(repository,))
        for repository in (first, second)
    ]
    for worker in workers:
        worker.start()
    ready.wait()
    for worker in workers:
        worker.join(10)

    assert failures == []
    providers = first.list_public()
    assert len(providers) == 1
    assert providers[0]["base_url"] == "https://sample.openai.azure.com/openai/v1"
    assert providers[0]["secret_source"] == "environment"
    assert providers[0]["env_var"] == "AZURE_OPENAI_API_KEY"


def test_invalid_legacy_endpoint_does_not_complete_seed(repositories, monkeypatch):
    first, second = repositories
    secret = "must-not-appear"
    monkeypatch.setenv(
        "AZURE_OPENAI_BASE_URL",
        f"https://sample.openai.azure.com/openai?api-version=preview&api-key={secret}",
    )

    with pytest.raises(ValueError, match="AZURE_OPENAI_BASE_URL cannot be imported") as exc_info:
        first.open()

    assert secret not in str(exc_info.value)
    assert DATABASE_URL is not None
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        assert (
            conn.execute(
                "SELECT value FROM provider_repository_metadata WHERE key = 'initial_seed_completed'"
            ).fetchone()
            is None
        )

    monkeypatch.setenv("AZURE_OPENAI_BASE_URL", "https://sample.openai.azure.com/openai")
    second.open()

    providers = second.list_public()
    assert len(providers) == 1
    assert providers[0]["base_url"] == "https://sample.openai.azure.com/openai/v1"


def test_interactive_provider_rejects_legacy_openai_path(repositories):
    first, _ = repositories
    first.open()

    with pytest.raises(ValueError, match="base_url must be a Foundry HTTPS resource endpoint"):
        first.create(
            {
                "name": "Azure OpenAI",
                "kind": "foundry",
                "base_url": "https://sample.openai.azure.com/openai",
                "default_model": "gpt-test",
                "api_key": "test-key",
            }
        )


def test_concurrent_default_changes_leave_exactly_one_default(repositories):
    first, second = repositories
    first.open()
    second.open()
    ready = threading.Barrier(3)
    failures: list[Exception] = []

    def create(repository: ProviderRepository, name: str) -> None:
        try:
            ready.wait()
            repository.create(
                {
                    "name": name,
                    "kind": "foundry",
                    "base_url": "https://sample.openai.azure.com/openai/v1",
                    "default_model": "gpt-test",
                    "api_key": "test-key",
                    "is_default": True,
                }
            )
        except Exception as exc:  # asserted below after both workers finish
            failures.append(exc)

    workers = [
        threading.Thread(target=create, args=(first, "first")),
        threading.Thread(target=create, args=(second, "second")),
    ]
    for worker in workers:
        worker.start()
    ready.wait()
    for worker in workers:
        worker.join(10)

    assert failures == []
    assert sum(provider["is_default"] for provider in first.list_public()) == 1


@pytest.mark.parametrize("endpoint", ["", "https://sample.openai.azure.com/openai/v1"])
def test_completed_seed_ignores_invalid_legacy_environment(repositories, monkeypatch, endpoint):
    first, second = repositories
    monkeypatch.setenv("AZURE_OPENAI_BASE_URL", endpoint)
    first.open()
    saved = first.list_public()

    monkeypatch.setenv("AZURE_OPENAI_BASE_URL", "https://invalid.example/openai")
    second.open()

    assert second.list_public() == saved
