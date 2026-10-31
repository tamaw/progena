"""Protocol and protected-file invariants for the local OAuth helper."""

import base64
import hashlib
import stat
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import urlopen

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from src.progena.settings import chatgpt_cli, providers
from src.progena.settings import chatgpt_oauth as oauth
from src.progena.settings.providers import ProviderRepository


@pytest.fixture
def signing_key(monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(
        oauth.jwt,
        "PyJWKClient",
        lambda *args, **kwargs: SimpleNamespace(
            get_signing_key_from_jwt=lambda token: SimpleNamespace(key=key.public_key())
        ),
    )
    return key


def _claims(**overrides):
    now = datetime.now(UTC)
    return {
        "iss": oauth.CHATGPT_ISSUER,
        "aud": "oaiapp_test",
        "sub": "subject-one",
        "email": "test@example.invalid",
        "iat": now,
        "exp": now + timedelta(hours=1),
        "nonce": "expected-nonce",
        **overrides,
    }


@pytest.mark.parametrize(
    "overrides",
    [
        {"nonce": "wrong-nonce"},
        {"aud": "another-client"},
        {"iss": "https://example.invalid"},
        {"exp": datetime(2000, 1, 1, tzinfo=UTC)},
    ],
)
def test_id_token_identity_and_attempt_boundaries(signing_key, overrides):
    token = jwt.encode(_claims(**overrides), signing_key, algorithm="RS256")
    with pytest.raises(oauth.ChatGPTOAuthError):
        oauth.validate_id_token(
            token,
            client_id="oaiapp_test",
            nonce="expected-nonce",
            configuration={"jwks_uri": "https://auth.openai.com/test-jwks"},
        )


def test_local_loopback_signin_validates_pkce_and_persists_protected_record(
    monkeypatch, signing_key, tmp_path
):
    config = {
        "authorization_endpoint": "https://auth.openai.com/api/accounts/authorize",
        "token_endpoint": "https://auth.openai.com/api/accounts/oauth/token",
        "jwks_uri": "https://auth.openai.com/test-jwks",
    }
    monkeypatch.setattr(oauth, "openid_configuration", lambda: config)
    attempt = {}

    def open_browser(url):
        query = parse_qs(urlsplit(url).query)
        attempt.update({key: values[0] for key, values in query.items()})
        callback = attempt["redirect_uri"]
        assert urlsplit(callback).hostname == "127.0.0.1"
        with urlopen(
            callback
            + "?"
            + urlencode(
                {"state": attempt["state"], "code": "test-code", "client_id": "oaiapp_test"}
            ),
            timeout=5,
        ) as response:
            assert response.status == 200
        return True

    def exchange(**kwargs):
        challenge = (
            base64.urlsafe_b64encode(
                hashlib.sha256(kwargs["code_verifier"].encode("ascii")).digest()
            )
            .rstrip(b"=")
            .decode("ascii")
        )
        assert challenge == attempt["code_challenge"]
        assert kwargs["redirect_uri"] == attempt["redirect_uri"]
        assert kwargs["client_id"] == "oaiapp_test"
        return {
            "id_token": jwt.encode(_claims(nonce=attempt["nonce"]), signing_key, algorithm="RS256"),
            "access_token": "synthetic-access",
            "refresh_token": "synthetic-refresh",
            "token_type": "Bearer",
            "expires_in": 3600,
            "scope": " ".join(oauth.REQUIRED_SCOPES),
        }

    monkeypatch.setattr(oauth.webbrowser, "open", open_browser)
    monkeypatch.setattr(oauth, "exchange_authorization_code", exchange)
    credential = oauth.authorize_with_browser(
        host_id="urn:uuid:00000000-0000-4000-8000-000000000001",
        agent_name="Progena",
        timeout_seconds=5,
    )
    assert credential.subject == "subject-one"
    assert credential.plan_enabled
    assert attempt["client_id"] == oauth.DYNAMIC_CLIENT_ID
    path = tmp_path / "credentials.json"
    before = stat.S_IMODE(tmp_path.stat().st_mode)
    oauth.write_protected_credential(path, credential)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(tmp_path.stat().st_mode) == before
    assert oauth.load_protected_credential(path).client_id == "oaiapp_test"
    path.chmod(0o644)
    with pytest.raises(ValueError):
        oauth.load_protected_credential(path)


@pytest.mark.parametrize(
    ("enable_plan_usage", "expected_prompt"),
    [(False, None), (True, "consent")],
)
def test_returning_signin_requests_consent_only_for_plan_usage_recovery(
    monkeypatch, signing_key, enable_plan_usage, expected_prompt
):
    config = {
        "authorization_endpoint": "https://auth.openai.com/api/accounts/authorize",
        "token_endpoint": "https://auth.openai.com/api/accounts/oauth/token",
        "jwks_uri": "https://auth.openai.com/test-jwks",
    }
    existing = oauth.ChatGPTCredential(
        email="test@example.invalid",
        issuer=oauth.CHATGPT_ISSUER,
        subject="subject-one",
        client_id="oaiapp_test",
        ext_agent_host_id="urn:uuid:00000000-0000-4000-8000-000000000001",
        id_token="retained-id-token",
        access_token="old-access",
        refresh_token="old-refresh",
        token_type="Bearer",
        expires_in=3600,
        scopes=tuple(scope for scope in oauth.REQUIRED_SCOPES if scope != oauth.CHATGPT_PLAN_SCOPE),
        saved_at=datetime.now(UTC),
        access_token_expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    attempt = {}

    def open_browser(url):
        query = parse_qs(urlsplit(url).query)
        attempt.update({key: values[0] for key, values in query.items()})
        with urlopen(
            attempt["redirect_uri"]
            + "?"
            + urlencode({"state": attempt["state"], "code": "test-code"}),
            timeout=5,
        ):
            pass
        return True

    def exchange(**kwargs):
        assert kwargs["client_id"] == existing.client_id
        return {
            "id_token": jwt.encode(_claims(nonce=attempt["nonce"]), signing_key, algorithm="RS256"),
            "access_token": "synthetic-access",
            "refresh_token": "synthetic-refresh",
            "token_type": "Bearer",
            "expires_in": 3600,
            "scope": " ".join(
                oauth.REQUIRED_SCOPES
                if enable_plan_usage
                else tuple(
                    scope for scope in oauth.REQUIRED_SCOPES if scope != oauth.CHATGPT_PLAN_SCOPE
                )
            ),
        }

    monkeypatch.setattr(oauth, "openid_configuration", lambda: config)
    monkeypatch.setattr(oauth.webbrowser, "open", open_browser)
    monkeypatch.setattr(oauth, "exchange_authorization_code", exchange)

    credential = oauth.authorize_with_browser(
        host_id=existing.ext_agent_host_id,
        agent_name="Progena",
        existing=existing,
        enable_plan_usage=enable_plan_usage,
        timeout_seconds=5,
    )

    assert attempt["id_token_hint"] == existing.id_token
    assert attempt.get("prompt") == expected_prompt
    assert attempt["scope"] == " ".join(oauth.REQUIRED_SCOPES)
    assert credential.plan_enabled is enable_plan_usage


def test_enable_plan_usage_cli_requests_reauthorization_consent(monkeypatch, tmp_path):
    credential_file = tmp_path / "credentials.json"
    credential_file.touch()
    existing = object()
    authorize_kwargs = {}

    monkeypatch.setattr(chatgpt_cli, "load_protected_credential", lambda path: existing)
    monkeypatch.setattr(chatgpt_cli, "load_or_create_host_id", lambda path: "host-id")
    monkeypatch.setattr(
        chatgpt_cli,
        "authorize_with_browser",
        lambda **kwargs: authorize_kwargs.update(kwargs) or existing,
    )
    monkeypatch.setattr(chatgpt_cli, "write_protected_credential", lambda path, credential: None)

    args = chatgpt_cli._parser().parse_args(
        [
            "sign-in",
            "--credential-file",
            str(credential_file),
            "--reauthorize",
            "--enable-plan-usage",
        ]
    )
    chatgpt_cli._run_sign_in(args)

    assert authorize_kwargs["existing"] is existing
    assert authorize_kwargs["enable_plan_usage"] is True


def test_wrong_callback_state_never_exchanges_code(monkeypatch):
    monkeypatch.setattr(
        oauth,
        "openid_configuration",
        lambda: {"authorization_endpoint": "https://auth.openai.com/api/accounts/authorize"},
    )

    def open_browser(url):
        callback = parse_qs(urlsplit(url).query)["redirect_uri"][0]
        with urlopen(callback + "?state=wrong&code=not-exchangeable", timeout=5):
            pass
        return True

    def forbidden_exchange(**kwargs):
        pytest.fail("A callback from another attempt must not exchange a code")

    monkeypatch.setattr(oauth.webbrowser, "open", open_browser)
    monkeypatch.setattr(oauth, "exchange_authorization_code", forbidden_exchange)
    with pytest.raises(oauth.ChatGPTOAuthError, match="state validation"):
        oauth.authorize_with_browser(
            host_id="urn:uuid:00000000-0000-4000-8000-000000000001",
            agent_name="Progena",
            timeout_seconds=5,
        )


@pytest.mark.parametrize(
    ("expires_after", "rejected_token", "returns_bearer"),
    [
        (timedelta(seconds=90), None, True),
        (timedelta(seconds=-1), None, False),
        (timedelta(seconds=90), "access-token", False),
    ],
)
def test_access_token_uses_unexpired_bearer_when_refresh_is_temporarily_unavailable(
    monkeypatch, expires_after, rejected_token, returns_bearer
):
    now = datetime(2026, 1, 1, tzinfo=UTC)
    credential = oauth.ChatGPTCredential(
        email="test@example.invalid",
        issuer=oauth.CHATGPT_ISSUER,
        subject="subject-one",
        client_id="oaiapp_test",
        ext_agent_host_id="host-id",
        id_token="id-token",
        access_token="access-token",
        refresh_token="refresh-token",
        token_type="Bearer",
        expires_in=3600,
        scopes=oauth.REQUIRED_SCOPES,
        saved_at=now - timedelta(minutes=1),
        access_token_expires_at=now + expires_after,
        refresh_available_at=now + timedelta(seconds=30),
    )
    repository = object.__new__(ProviderRepository)
    repository._pool = MagicMock()
    monkeypatch.setattr(repository, "chatgpt_host_id", lambda: "host-id")
    monkeypatch.setattr(repository, "_get_row", lambda *_args, **_kwargs: {"kind": "chatgpt"})
    monkeypatch.setattr(
        repository,
        "_credential_from_row",
        lambda *_args, **_kwargs: credential,
    )
    monkeypatch.setattr(providers, "_utc_now", lambda: now)

    if returns_bearer:
        assert repository._chatgpt_access_token("provider-1") == credential.access_token
    else:
        with pytest.raises(ValueError, match="expiring before it can be refreshed"):
            repository._chatgpt_access_token("provider-1", rejected_token=rejected_token)
