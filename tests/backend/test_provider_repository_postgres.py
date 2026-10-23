"""Real-Postgres concurrency coverage for persisted provider settings.

Opt-in on a scratch database (``PROGENA_TEST_DATABASE_URL``).
"""

from __future__ import annotations

import os
import threading
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from cryptography.fernet import Fernet
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from src.progena.settings.chatgpt_oauth import ChatGPTCredential, ChatGPTOAuthHttpError
from src.progena.settings.providers import ProviderRepository

DATABASE_URL = (os.getenv("PROGENA_TEST_DATABASE_URL") or "").strip() or None

pytestmark = pytest.mark.skipif(
    DATABASE_URL is None, reason="requires PROGENA_TEST_DATABASE_URL (a scratch database)"
)


@pytest.fixture
def repositories(monkeypatch):
    assert DATABASE_URL is not None
    monkeypatch.delenv("PROGENA_DATABASE_URL", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_BASE_URL", raising=False)
    monkeypatch.setenv("PROGENA_PROVIDER_ENCRYPTION_KEY", Fernet.generate_key().decode("ascii"))
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


def test_chatgpt_provider_starts_unconnected_without_a_placeholder_model(repositories):
    first, _ = repositories
    first.open()

    provider = first.create({"name": "ChatGPT account", "kind": "chatgpt"})

    assert provider["base_url"] == "https://api.openai.com/v1"
    assert provider["default_model"] is None
    assert provider["connection_status"] == "unconnected"
    assert provider["account"] is None
    assert provider["granted_scopes"] == []
    assert provider["has_api_key"] is False
    with pytest.raises(ValueError, match="default_model"):
        first.resolve(provider["id"])


def _connected_chatgpt_provider(
    repository: ProviderRepository, *, expires_at: datetime | None = None
) -> tuple[dict[str, object], ChatGPTCredential]:
    provider = repository.create(
        {
            "name": "ChatGPT account",
            "kind": "chatgpt",
            "default_model": "gpt-test",
        }
    )
    saved_at = datetime.now(UTC)
    credential = ChatGPTCredential(
        email="user@example.com",
        issuer="https://auth.openai.com",
        subject="subject",
        client_id="oaiapp_test",
        ext_agent_host_id="urn:uuid:test",
        id_token="id-token",
        access_token="access-token",
        refresh_token="refresh-token",
        token_type="Bearer",
        expires_in=3600,
        scopes=("openid", "offline_access", "resource.invoke", "chatgpt.tokens.use.direct"),
        saved_at=saved_at,
        access_token_expires_at=expires_at or saved_at + timedelta(hours=1),
    )
    with repository._pool.connection() as conn, conn.transaction(), conn.cursor() as cur:
        repository._store_chatgpt_credential(cur, str(provider["id"]), credential)
    return provider, credential


def test_chatgpt_access_read_with_one_connection_pool_has_no_nested_checkout(repositories):
    first, _ = repositories
    first.open()
    first.close()
    assert DATABASE_URL is not None
    first._pool = ConnectionPool(
        conninfo=DATABASE_URL,
        kwargs={"autocommit": True, "row_factory": dict_row},
        min_size=1,
        max_size=1,
        open=False,
    )
    first.open()
    provider, credential = _connected_chatgpt_provider(first)
    ready = threading.Barrier(3)
    tokens: list[str] = []
    failures: list[Exception] = []

    def read_token() -> None:
        try:
            ready.wait()
            tokens.append(first._chatgpt_access_token(str(provider["id"])))
        except Exception as exc:  # asserted after both workers leave the one-connection pool
            failures.append(exc)

    workers = [threading.Thread(target=read_token) for _ in range(2)]
    for worker in workers:
        worker.start()
    ready.wait()
    for worker in workers:
        worker.join(5)

    assert all(not worker.is_alive() for worker in workers)
    assert failures == []
    assert tokens == [credential.access_token, credential.access_token]


def test_rejected_chatgpt_token_refresh_reuses_another_process_rotation(repositories, monkeypatch):
    first, second = repositories
    first.open()
    second.open()
    provider, credential = _connected_chatgpt_provider(first)
    refresh_tokens: list[str] = []

    def refresh(*, client_id: str, refresh_token: str):
        assert client_id == credential.client_id
        refresh_tokens.append(refresh_token)
        return {
            "access_token": "rotated-access-token",
            "refresh_token": "rotated-refresh-token",
            "token_type": "Bearer",
            "expires_in": 3600,
            "scope": " ".join(credential.scopes),
        }

    monkeypatch.setattr("src.progena.settings.providers.refresh_token_response", refresh)

    rotated = first._refresh_chatgpt_access_token_after_rejection(
        str(provider["id"]),
        credential.access_token,
    )
    reused = second._refresh_chatgpt_access_token_after_rejection(
        str(provider["id"]),
        credential.access_token,
    )

    assert rotated == reused == "rotated-access-token"
    assert refresh_tokens == [credential.refresh_token]


def test_terminal_chatgpt_refresh_error_commits_reauthentication_state(repositories, monkeypatch):
    first, _ = repositories
    first.open()
    provider, _ = _connected_chatgpt_provider(first, expires_at=datetime.now(UTC))

    def invalid_grant(**_kwargs):
        raise ChatGPTOAuthHttpError(400, "invalid_grant")

    monkeypatch.setattr("src.progena.settings.providers.refresh_token_response", invalid_grant)

    with pytest.raises(ValueError, match="sign in again"):
        first.resolve(str(provider["id"]))

    public = first.list_public()[0]
    assert public["connection_status"] == "reauth_required"
    assert public["has_api_key"] is False
    assert public["granted_scopes"] == []


def test_chatgpt_refresh_persists_rotated_tokens_before_id_token_validation(
    repositories, monkeypatch
):
    first, _ = repositories
    first.open()
    provider, credential = _connected_chatgpt_provider(first, expires_at=datetime.now(UTC))
    refresh_tokens: list[str] = []
    validation_attempts: list[str] = []

    def refresh(*, client_id: str, refresh_token: str):
        assert client_id == credential.client_id
        refresh_tokens.append(refresh_token)
        return {
            "access_token": "rotated-access-token",
            "refresh_token": "rotated-refresh-token",
            "id_token": "rotated-id-token",
            "token_type": "Bearer",
            "expires_in": 3600,
            "scope": " ".join(credential.scopes),
        }

    def validate(id_token: str, *, client_id: str, nonce: None):
        assert client_id == credential.client_id
        assert nonce is None
        validation_attempts.append(id_token)
        if len(validation_attempts) == 1:
            raise ConnectionError("JWKS is temporarily unavailable")
        return {"sub": credential.subject, "email": credential.email}

    monkeypatch.setattr("src.progena.settings.providers.refresh_token_response", refresh)
    monkeypatch.setattr("src.progena.settings.providers.validate_id_token", validate)

    with pytest.raises(ConnectionError, match="JWKS is temporarily unavailable"):
        first.resolve(str(provider["id"]))

    with first._pool.connection() as conn, conn.cursor() as cur:
        pending = first._get_row(cur, str(provider["id"]))
    assert pending["chatgpt_status"] == "pending_validation"
    assert first._decrypt(pending["chatgpt_access_token_ciphertext"]) == "rotated-access-token"
    assert first._decrypt(pending["chatgpt_refresh_token_ciphertext"]) == "rotated-refresh-token"

    resolved = first.resolve(str(provider["id"]))

    assert resolved["api_key"] == "rotated-access-token"
    assert refresh_tokens == [credential.refresh_token]
    assert validation_attempts == ["rotated-id-token", "rotated-id-token"]
    assert first.list_public()[0]["connection_status"] == "connected"


def test_stale_chatgpt_import_cannot_replace_rotated_tokens(repositories, monkeypatch):
    first, _ = repositories
    first.open()
    provider, current = _connected_chatgpt_provider(first)
    stale = ChatGPTCredential(
        email=current.email,
        issuer=current.issuer,
        subject=current.subject,
        client_id=current.client_id,
        ext_agent_host_id=current.ext_agent_host_id,
        id_token="old-id-token",
        access_token="old-access-token",
        refresh_token="old-refresh-token",
        token_type=current.token_type,
        expires_in=current.expires_in,
        scopes=current.scopes,
        saved_at=current.saved_at - timedelta(seconds=1),
        access_token_expires_at=current.access_token_expires_at,
    )
    monkeypatch.setattr(
        "src.progena.settings.providers.validate_imported_credential", lambda _payload: current
    )
    revision = first.list_public()[0]["revision"]
    idempotent = first.import_chatgpt_credential({}, provider_id=str(provider["id"]))
    assert idempotent["revision"] == revision

    monkeypatch.setattr(
        "src.progena.settings.providers.validate_imported_credential", lambda _payload: stale
    )
    with pytest.raises(ValueError, match="predates"):
        first.import_chatgpt_credential({}, provider_id=str(provider["id"]))

    assert first.resolve(str(provider["id"]))["api_key"] == current.access_token
