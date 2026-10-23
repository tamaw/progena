"""Persisted Responses API provider connections shared by backend and runtime."""

from __future__ import annotations

import asyncio
import hmac
import json
import os
from collections.abc import Mapping
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Any, LiteralString, cast
from urllib.parse import parse_qsl, urlsplit, urlunsplit
from uuid import uuid4

import aiohttp
import psycopg
from cryptography.fernet import Fernet, InvalidToken
from psycopg import errors, sql
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from src.progena.settings.chatgpt_oauth import (
    CHATGPT_PLAN_SCOPE,
    CHATGPT_RESOURCE,
    ChatGPTCredential,
    ChatGPTOAuthHttpError,
    credential_from_token_response,
    refresh_token_response,
    revoke_refresh_token,
    validate_id_token,
    validate_imported_credential,
)

GO_RESPONSES_MODELS = (
    "grok-4.7",
    "gpt-6-luna",
    "gpt-5.6-luna",
    "muse-spark-1.3-contributor",
    "muse-spark-1.2-contributor",
)
GO_BASE_URL = "https://opencode.ai/inference/go/openai/v1"
CHATGPT_BASE_URL = CHATGPT_RESOURCE

_PROVIDER_KINDS = frozenset({"foundry", "opencode_go", "chatgpt"})
_SECRET_SOURCES = frozenset({"database", "environment"})
_AZURE_HOST_SUFFIXES = (
    ".openai.azure.com",
    ".services.ai.azure.com",
    ".cognitiveservices.azure.com",
)
_CHATGPT_REFRESH_LEEWAY = timedelta(minutes=2)
_UNUSABLE_REFRESH_CODES = frozenset(
    {
        "invalid_grant",
        "invalid_refresh_token",
        "token_expired",
        "refresh_token_expired",
        "refresh_token_invalidated",
        "refresh_token_reused",
    }
)
_CHATGPT_PENDING_VALIDATION = "pending_validation"
_CHATGPT_PLAN_REAUTHORIZE_COMMAND = (
    "ChatGPT plan usage is not enabled; run chatgpt_cli sign-in --reauthorize --enable-plan-usage"
)
_default_repository: ProviderRepository | None = None


def provider_database_url(runtime_url: str | None = None) -> str:
    """Derive the backend-owned provider database URL from the runtime URL."""
    source = (runtime_url or os.getenv("PROGENA_DATABASE_URL") or "").strip()
    if not source:
        return ""
    database_name = (os.getenv("BACKEND_DATABASE_NAME") or "backend").strip() or "backend"
    parts = urlsplit(source)
    if not parts.scheme or not parts.netloc:
        raise ValueError("PROGENA_DATABASE_URL must be a database URL")
    return urlunsplit(parts._replace(path=f"/{database_name}"))


def _ensure_database_exists(admin_url: str, database_name: str) -> None:
    """Create the backend database once, outside a transaction."""
    if not admin_url or not database_name:
        return
    with psycopg.connect(admin_url, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (database_name,))
        if cur.fetchone() is None:
            with suppress(errors.DuplicateDatabase):
                cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))


def _allowed_environment_variable(name: object) -> str:
    if not isinstance(name, str):
        raise ValueError("env_var must be a permitted API key environment variable")
    normalized = name.strip()
    if normalized in {"AZURE_OPENAI_API_KEY", "OPENCODE_GO_API_KEY"} or (
        normalized.startswith("PROGENA_PROVIDER_") and normalized.endswith("_API_KEY")
    ):
        return normalized
    raise ValueError("env_var must be a permitted API key environment variable")


def _normalize_foundry_base_url(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("base_url must be a Foundry HTTPS endpoint")
    parsed = urlsplit(value.strip())
    host = (parsed.hostname or "").lower()
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("base_url must be a Foundry HTTPS resource endpoint") from exc
    if (
        parsed.scheme != "https"
        or not host.endswith(_AZURE_HOST_SUFFIXES)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or port is not None
        or parsed.path.rstrip("/") not in {"", "/openai/v1"}
    ):
        raise ValueError("base_url must be a Foundry HTTPS resource endpoint")
    return urlunsplit(("https", parsed.netloc.lower(), "/openai/v1", "", ""))


def _normalize_legacy_foundry_base_url(value: object) -> str:
    """Normalize historic Responses endpoint URLs during one-time env seeding."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("base_url must be a Foundry HTTPS endpoint")
    parsed = urlsplit(value.strip())
    query = parse_qsl(parsed.query, keep_blank_values=True)
    if parsed.fragment or (
        parsed.query
        and (not query or any(key != "api-version" or not version for key, version in query))
    ):
        raise ValueError("base_url must be a Foundry HTTPS resource endpoint")
    path = parsed.path.rstrip("/")
    if path.endswith("/responses"):
        path = path[: -len("/responses")]
    if path == "/openai":
        path = "/openai/v1"
    return _normalize_foundry_base_url(urlunsplit((parsed.scheme, parsed.netloc, path, "", "")))


def _normalize_model(value: object, *, kind: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("default_model must be a non-empty string")
    model = value.strip()
    if kind == "opencode_go" and model not in GO_RESPONSES_MODELS:
        raise ValueError("default_model is not supported by OpenCode Go")
    return model


def _chatgpt_account_public(row: Mapping[str, Any]) -> dict[str, str] | None:
    fields = {
        "email": row.get("chatgpt_email"),
        "subject": row.get("chatgpt_subject"),
        "client_id": row.get("chatgpt_client_id"),
        "issuer": row.get("chatgpt_issuer"),
    }
    account = {key: value for key, value in fields.items() if isinstance(value, str) and value}
    return account or None


def _row_public(row: Mapping[str, Any]) -> dict[str, Any]:
    source = str(row["secret_source"])
    env_var = row.get("env_var")
    kind = str(row["kind"])
    has_api_key = bool(row.get("api_key_ciphertext"))
    if source == "environment" and isinstance(env_var, str):
        has_api_key = bool(os.getenv(env_var))
    default_model = row.get("default_model")
    result: dict[str, Any] = {
        "id": str(row["id"]),
        "name": str(row["name"]),
        "kind": kind,
        "base_url": str(row["base_url"]),
        "default_model": default_model if isinstance(default_model, str) else None,
        "secret_source": source,
        "env_var": env_var if source == "environment" else None,
        "has_api_key": has_api_key,
        "is_default": bool(row["is_default"]),
        "revision": int(row["revision"]),
    }
    if kind == "chatgpt":
        scopes = row.get("chatgpt_scopes")
        result.update(
            {
                "connection_status": str(row.get("chatgpt_status") or "unconnected"),
                "account": _chatgpt_account_public(row),
                "granted_scopes": list(scopes) if isinstance(scopes, list) else [],
                "plan_enabled": CHATGPT_PLAN_SCOPE in scopes if isinstance(scopes, list) else False,
                "has_api_key": bool(row.get("chatgpt_access_token_ciphertext")),
            }
        )
    return result


def _utc_now() -> datetime:
    return datetime.now(UTC)


class ProviderRepository:
    """Synchronous Postgres repository for connection settings and encrypted credentials."""

    def __init__(self, database_url: str) -> None:
        if not database_url:
            raise ValueError("provider database URL is required")
        self._database_url = database_url
        self._pool = ConnectionPool(
            conninfo=database_url,
            kwargs={"autocommit": True, "row_factory": dict_row},
            open=False,
        )

    def __repr__(self) -> str:
        return "ProviderRepository()"

    def open(self) -> None:
        runtime_url = (os.getenv("PROGENA_DATABASE_URL") or "").strip()
        if runtime_url:
            target_name = urlsplit(self._database_url).path.strip("/")
            _ensure_database_exists(runtime_url, target_name)
        self._pool.open()
        self._ensure_tables()
        self._seed_initial_provider()

    def close(self) -> None:
        self._pool.close()

    def _ensure_tables(self) -> None:
        with self._pool.connection() as conn, conn.transaction(), conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(hashtext('provider_repository_schema'))")
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS provider_connection (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE,
                    kind TEXT NOT NULL CHECK (kind IN ('foundry', 'opencode_go', 'chatgpt')),
                    base_url TEXT NOT NULL,
                    default_model TEXT,
                    secret_source TEXT NOT NULL CHECK (secret_source IN ('database', 'environment')),
                    env_var TEXT,
                    api_key_ciphertext TEXT,
                    is_default BOOLEAN NOT NULL DEFAULT FALSE,
                    revision INTEGER NOT NULL DEFAULT 1,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    chatgpt_issuer TEXT,
                    chatgpt_subject TEXT,
                    chatgpt_email TEXT,
                    chatgpt_client_id TEXT,
                    chatgpt_access_token_ciphertext TEXT,
                    chatgpt_refresh_token_ciphertext TEXT,
                    chatgpt_id_token_ciphertext TEXT,
                    chatgpt_access_token_expires_at TIMESTAMPTZ,
                    chatgpt_credentials_saved_at TIMESTAMPTZ,
                    chatgpt_refresh_available_at TIMESTAMPTZ,
                    chatgpt_scopes TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
                    chatgpt_status TEXT NOT NULL DEFAULT 'unconnected',
                    CHECK ((secret_source = 'environment' AND env_var IS NOT NULL) OR secret_source = 'database')
                )
                """
            )
            cur.execute("ALTER TABLE provider_connection ALTER COLUMN default_model DROP NOT NULL")
            for column, definition in (
                ("chatgpt_issuer", "TEXT"),
                ("chatgpt_subject", "TEXT"),
                ("chatgpt_email", "TEXT"),
                ("chatgpt_client_id", "TEXT"),
                ("chatgpt_access_token_ciphertext", "TEXT"),
                ("chatgpt_refresh_token_ciphertext", "TEXT"),
                ("chatgpt_id_token_ciphertext", "TEXT"),
                ("chatgpt_access_token_expires_at", "TIMESTAMPTZ"),
                ("chatgpt_credentials_saved_at", "TIMESTAMPTZ"),
                ("chatgpt_refresh_available_at", "TIMESTAMPTZ"),
                ("chatgpt_scopes", "TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[]"),
                ("chatgpt_status", "TEXT NOT NULL DEFAULT 'unconnected'"),
            ):
                cur.execute(
                    sql.SQL(
                        "ALTER TABLE provider_connection ADD COLUMN IF NOT EXISTS {} {}"
                    ).format(sql.Identifier(column), sql.SQL(cast(LiteralString, definition)))
                )
            cur.execute(
                "ALTER TABLE provider_connection DROP CONSTRAINT IF EXISTS provider_connection_kind_check"
            )
            cur.execute(
                """
                ALTER TABLE provider_connection
                ADD CONSTRAINT provider_connection_kind_check
                CHECK (kind IN ('foundry', 'opencode_go', 'chatgpt'))
                """
            )
            cur.execute(
                "ALTER TABLE provider_connection DROP CONSTRAINT IF EXISTS provider_connection_chatgpt_status_check"
            )
            cur.execute(
                """
                ALTER TABLE provider_connection
                ADD CONSTRAINT provider_connection_chatgpt_status_check
                CHECK (chatgpt_status IN ('unconnected', 'connected', 'plan_disabled',
                                          'pending_validation', 'reauth_required', 'disconnected'))
                """
            )
            cur.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS provider_connection_one_default
                ON provider_connection ((is_default)) WHERE is_default
                """
            )
            cur.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS provider_connection_chatgpt_registration
                ON provider_connection (chatgpt_issuer, chatgpt_subject, chatgpt_client_id)
                WHERE kind = 'chatgpt'
                  AND chatgpt_issuer IS NOT NULL
                  AND chatgpt_subject IS NOT NULL
                  AND chatgpt_client_id IS NOT NULL
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS provider_repository_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )

    def _seed_initial_provider(self) -> None:
        """Capture the legacy Azure configuration exactly once for this installation."""
        endpoint = (os.getenv("AZURE_OPENAI_BASE_URL") or "").strip()
        model = (os.getenv("PROGENA_MODEL_DEFAULT") or "gpt-chat-latest").strip()
        with self._pool.connection() as conn, conn.transaction(), conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO provider_repository_metadata (key, value)
                VALUES ('initial_seed_completed', 'true')
                ON CONFLICT (key) DO NOTHING
                RETURNING key
                """
            )
            if cur.fetchone() is None or not endpoint:
                return
            try:
                base_url = _normalize_legacy_foundry_base_url(endpoint)
            except ValueError as exc:
                raise ValueError(
                    "AZURE_OPENAI_BASE_URL cannot be imported; "
                    "expected a Foundry HTTPS resource endpoint"
                ) from exc
            cur.execute(
                """
                INSERT INTO provider_connection
                    (id, name, kind, base_url, default_model, secret_source, env_var, is_default)
                VALUES (%s, %s, 'foundry', %s, %s, 'environment', 'AZURE_OPENAI_API_KEY', TRUE)
                ON CONFLICT (name) DO NOTHING
                """,
                (str(uuid4()), "Azure OpenAI", base_url, model or "gpt-chat-latest"),
            )

    def _fernet(self) -> Fernet:
        key = (os.getenv("PROGENA_PROVIDER_ENCRYPTION_KEY") or "").strip()
        if not key:
            raise ValueError("PROGENA_PROVIDER_ENCRYPTION_KEY is required for database credentials")
        try:
            return Fernet(key.encode("ascii"))
        except (ValueError, TypeError) as exc:
            raise ValueError("PROGENA_PROVIDER_ENCRYPTION_KEY is invalid") from exc

    def _encrypt(self, api_key: object) -> str:
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("api_key must be a non-empty string")
        return self._fernet().encrypt(api_key.strip().encode("utf-8")).decode("ascii")

    def _decrypt(self, ciphertext: object) -> str:
        if not isinstance(ciphertext, str) or not ciphertext:
            raise ValueError("provider has no API key configured")
        try:
            return self._fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
        except (InvalidToken, UnicodeDecodeError) as exc:
            raise ValueError("provider credential cannot be decrypted") from exc

    @staticmethod
    def _validated_common(
        data: Mapping[str, Any], *, existing: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        kind = str(data.get("kind", existing["kind"] if existing else "")).strip()
        if kind not in _PROVIDER_KINDS:
            raise ValueError("kind must be 'foundry', 'opencode_go', or 'chatgpt'")
        name_value = data.get("name", existing["name"] if existing else "")
        if not isinstance(name_value, str) or not name_value.strip():
            raise ValueError("name must be a non-empty string")
        name = name_value.strip()
        if kind == "opencode_go":
            supplied_base = data.get("base_url", existing["base_url"] if existing else GO_BASE_URL)
            if supplied_base not in (None, "", GO_BASE_URL):
                raise ValueError("base_url is fixed for OpenCode Go")
            base_url = GO_BASE_URL
            default_model = _normalize_model(
                data.get("default_model", existing["default_model"] if existing else None),
                kind=kind,
            )
        elif kind == "chatgpt":
            supplied_base = data.get(
                "base_url", existing["base_url"] if existing else CHATGPT_BASE_URL
            )
            if supplied_base not in (None, "", CHATGPT_BASE_URL):
                raise ValueError("base_url is fixed for ChatGPT plan OAuth")
            base_url = CHATGPT_BASE_URL
            supplied_model = data.get(
                "default_model", existing.get("default_model") if existing else None
            )
            if supplied_model in (None, ""):
                default_model = None
            elif isinstance(supplied_model, str) and supplied_model.strip():
                default_model = supplied_model.strip()
            else:
                raise ValueError("default_model must be a non-empty string or null")
        else:
            base_url = _normalize_foundry_base_url(
                data.get("base_url", existing["base_url"] if existing else None)
            )
            default_model = _normalize_model(
                data.get("default_model", existing["default_model"] if existing else None),
                kind=kind,
            )
        source = str(
            data.get("secret_source", existing["secret_source"] if existing else "database")
        ).strip()
        if source not in _SECRET_SOURCES:
            raise ValueError("secret_source must be 'database' or 'environment'")
        env_var: str | None = None
        if kind == "chatgpt":
            if source != "database":
                raise ValueError("ChatGPT plan OAuth credentials are managed by the server")
            if data.get("env_var") not in (None, ""):
                raise ValueError("env_var is not valid for ChatGPT plan OAuth")
        elif source == "environment":
            env_var = _allowed_environment_variable(
                data.get("env_var", existing["env_var"] if existing else None)
            )
        return {
            "name": name,
            "kind": kind,
            "base_url": base_url,
            "default_model": default_model,
            "secret_source": source,
            "env_var": env_var,
        }

    def list_public(self) -> list[dict[str, Any]]:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM provider_connection ORDER BY name, id")
            rows = cur.fetchall()
        return [_row_public(row) for row in rows]

    def _get_row(self, cur: Any, provider_id: str, *, lock: bool = False) -> dict[str, Any]:
        query = (
            "SELECT * FROM provider_connection WHERE id = %s FOR UPDATE"
            if lock
            else "SELECT * FROM provider_connection WHERE id = %s"
        )
        cur.execute(query, (provider_id,))
        row = cur.fetchone()
        if row is None:
            raise LookupError("provider not found")
        return row

    def chatgpt_host_id(self) -> str:
        """Return this server's stable OpenAI host identity, generating it once."""
        with self._pool.connection() as conn, conn.transaction(), conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(hashtext('provider_chatgpt_host_id'))")
            cur.execute(
                "SELECT value FROM provider_repository_metadata WHERE key = 'chatgpt_ext_agent_host_id'"
            )
            existing = cur.fetchone()
            if existing is not None:
                return str(existing["value"])
            host_id = f"urn:uuid:{uuid4()}"
            cur.execute(
                "INSERT INTO provider_repository_metadata (key, value) VALUES (%s, %s)",
                ("chatgpt_ext_agent_host_id", host_id),
            )
            return host_id

    def create(self, data: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(data, dict):
            raise ValueError("provider body must be an object")
        allowed = {
            "name",
            "kind",
            "base_url",
            "default_model",
            "secret_source",
            "env_var",
            "api_key",
            "is_default",
        }
        unknown = set(data) - allowed
        if unknown:
            raise ValueError(f"unsupported provider fields: {sorted(unknown)}")
        fields = self._validated_common(data)
        api_key = data.get("api_key", ...)
        ciphertext: str | None = None
        if fields["kind"] == "chatgpt":
            if api_key is not ...:
                raise ValueError("api_key is not valid for ChatGPT plan OAuth")
        elif fields["secret_source"] == "database":
            if api_key is not ... and api_key is not None:
                ciphertext = self._encrypt(api_key)
            elif api_key is not ...:
                ciphertext = None
        elif api_key is not ...:
            raise ValueError("api_key is only valid for database credentials")
        requested_default = data.get("is_default")
        if requested_default is not None and not isinstance(requested_default, bool):
            raise ValueError("is_default must be a boolean")
        provider_id = str(uuid4())
        with self._pool.connection() as conn, conn.transaction(), conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(hashtext('provider_connection_default'))")
            cur.execute("SELECT id FROM provider_connection WHERE is_default FOR UPDATE")
            has_default = cur.fetchone() is not None
            is_default = not has_default or bool(requested_default)
            if is_default:
                cur.execute(
                    "UPDATE provider_connection SET is_default = FALSE, revision = revision + 1 WHERE is_default"
                )
            cur.execute(
                """
                INSERT INTO provider_connection
                    (id, name, kind, base_url, default_model, secret_source, env_var, api_key_ciphertext,
                     is_default, chatgpt_status)
                VALUES (%(id)s, %(name)s, %(kind)s, %(base_url)s, %(default_model)s, %(secret_source)s,
                        %(env_var)s, %(api_key_ciphertext)s, %(is_default)s, %(chatgpt_status)s)
                RETURNING *
                """,
                {
                    **fields,
                    "id": provider_id,
                    "api_key_ciphertext": ciphertext,
                    "is_default": is_default,
                    "chatgpt_status": "unconnected",
                },
            )
            row = cur.fetchone()
        assert row is not None
        return _row_public(row)

    def update(self, provider_id: str, data: dict[str, Any]) -> dict[str, Any]:
        return self._update(provider_id, data)

    def _update(
        self,
        provider_id: str,
        data: dict[str, Any],
        *,
        chatgpt_default_model: str | None = None,
    ) -> dict[str, Any]:
        if not isinstance(data, dict) or (not data and chatgpt_default_model is None):
            raise ValueError("provider patch must be a non-empty object")
        allowed = {
            "name",
            "kind",
            "base_url",
            "default_model",
            "secret_source",
            "env_var",
            "api_key",
            "is_default",
        }
        unknown = set(data) - allowed
        if unknown:
            raise ValueError(f"unsupported provider fields: {sorted(unknown)}")
        with self._pool.connection() as conn, conn.transaction(), conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(hashtext('provider_connection_default'))")
            existing = self._get_row(cur, provider_id, lock=True)
            fields = self._validated_common(data, existing=existing)
            if fields["kind"] != existing["kind"]:
                raise ValueError("provider kind cannot be changed")
            if fields["kind"] == "chatgpt":
                if "default_model" in data:
                    raise ValueError("select a ChatGPT model from the account catalog")
                if chatgpt_default_model is not None:
                    fields["default_model"] = chatgpt_default_model
            elif chatgpt_default_model is not None:
                raise ValueError("provider is not a ChatGPT plan OAuth provider")
            api_key = data.get("api_key", ...)
            ciphertext = existing.get("api_key_ciphertext")
            if fields["kind"] == "chatgpt":
                if api_key is not ...:
                    raise ValueError("api_key is not valid for ChatGPT plan OAuth")
            elif fields["secret_source"] == "database":
                if api_key is None:
                    ciphertext = None
                elif api_key is not ...:
                    ciphertext = self._encrypt(api_key)
            else:
                if api_key is not ...:
                    raise ValueError("api_key is only valid for database credentials")
                ciphertext = None
            requested_default = data.get("is_default", existing["is_default"])
            if not isinstance(requested_default, bool):
                raise ValueError("is_default must be a boolean")
            if not requested_default and bool(existing["is_default"]):
                raise ValueError("default provider must be switched before removal")
            if requested_default:
                cur.execute(
                    "UPDATE provider_connection SET is_default = FALSE, revision = revision + 1 WHERE is_default AND id <> %s",
                    (provider_id,),
                )
            cur.execute(
                """
                UPDATE provider_connection
                SET name = %(name)s, kind = %(kind)s, base_url = %(base_url)s,
                    default_model = %(default_model)s, secret_source = %(secret_source)s,
                    env_var = %(env_var)s, api_key_ciphertext = %(api_key_ciphertext)s,
                    is_default = %(is_default)s, revision = revision + 1, updated_at = now()
                WHERE id = %(id)s
                RETURNING *
                """,
                {
                    **fields,
                    "id": provider_id,
                    "api_key_ciphertext": ciphertext,
                    "is_default": requested_default,
                },
            )
            row = cur.fetchone()
        assert row is not None
        return _row_public(row)

    def _update_chatgpt_provider(
        self,
        provider_id: str,
        data: dict[str, Any],
        default_model: str | None,
    ) -> dict[str, Any]:
        """Commit an already catalog-validated model and provider patch together."""
        return self._update(provider_id, data, chatgpt_default_model=default_model)

    def delete(self, provider_id: str) -> None:
        with self._pool.connection() as conn, conn.transaction(), conn.cursor() as cur:
            row = self._get_row(cur, provider_id, lock=True)
            if row["is_default"]:
                raise ValueError("default provider must be switched before removal")
            cur.execute("DELETE FROM provider_connection WHERE id = %s", (provider_id,))

    def _credential_from_row(
        self,
        row: Mapping[str, Any],
        *,
        host_id: str,
        allow_pending_validation: bool = False,
    ) -> ChatGPTCredential:
        status = str(row.get("chatgpt_status") or "unconnected")
        if status != "connected" and not (
            allow_pending_validation and status == _CHATGPT_PENDING_VALIDATION
        ):
            if status == "plan_disabled":
                raise ValueError(_CHATGPT_PLAN_REAUTHORIZE_COMMAND)
            if status == "reauth_required":
                raise ValueError("ChatGPT account must sign in again")
            raise ValueError("ChatGPT account is not connected")
        expires_at = row.get("chatgpt_access_token_expires_at")
        if not isinstance(expires_at, datetime):
            raise ValueError("ChatGPT account has no access-token expiry")
        saved_at = row.get("chatgpt_credentials_saved_at")
        if not isinstance(saved_at, datetime):
            saved_at = row.get("updated_at")
        if not isinstance(saved_at, datetime):
            saved_at = _utc_now()
        scopes = row.get("chatgpt_scopes")
        if not isinstance(scopes, list):
            scopes = []
        return ChatGPTCredential(
            email=row.get("chatgpt_email") if isinstance(row.get("chatgpt_email"), str) else None,
            issuer=str(row.get("chatgpt_issuer") or ""),
            subject=str(row.get("chatgpt_subject") or ""),
            client_id=str(row.get("chatgpt_client_id") or ""),
            ext_agent_host_id=host_id,
            id_token=self._decrypt(row.get("chatgpt_id_token_ciphertext")),
            access_token=self._decrypt(row.get("chatgpt_access_token_ciphertext")),
            refresh_token=self._decrypt(row.get("chatgpt_refresh_token_ciphertext")),
            token_type="Bearer",  # nosec B106 - OAuth token type, not a secret
            expires_in=max(1, int((expires_at - saved_at).total_seconds())),
            scopes=tuple(str(scope) for scope in scopes),
            saved_at=saved_at,
            access_token_expires_at=expires_at,
            refresh_available_at=row.get("chatgpt_refresh_available_at"),
        )

    @staticmethod
    def _chatgpt_status(credential: ChatGPTCredential) -> str:
        return "connected" if credential.plan_enabled else "plan_disabled"

    def _store_chatgpt_credential(
        self,
        cur: Any,
        provider_id: str,
        credential: ChatGPTCredential,
        *,
        status: str | None = None,
    ) -> None:
        cur.execute(
            """
            UPDATE provider_connection
            SET chatgpt_issuer = %(issuer)s, chatgpt_subject = %(subject)s,
                chatgpt_email = %(email)s, chatgpt_client_id = %(client_id)s,
                chatgpt_access_token_ciphertext = %(access)s,
                chatgpt_refresh_token_ciphertext = %(refresh)s,
                chatgpt_id_token_ciphertext = %(id_token)s,
                chatgpt_access_token_expires_at = %(expires_at)s,
                chatgpt_credentials_saved_at = %(saved_at)s,
                chatgpt_refresh_available_at = %(refresh_available_at)s,
                chatgpt_scopes = %(scopes)s, chatgpt_status = %(status)s,
                revision = revision + 1, updated_at = now()
            WHERE id = %(provider_id)s
            """,
            {
                "issuer": credential.issuer,
                "subject": credential.subject,
                "email": credential.email,
                "client_id": credential.client_id,
                "access": self._encrypt(credential.access_token),
                "refresh": self._encrypt(credential.refresh_token),
                "id_token": self._encrypt(credential.id_token),
                "expires_at": credential.access_token_expires_at,
                "saved_at": credential.saved_at,
                "refresh_available_at": credential.refresh_available_at,
                "scopes": list(credential.scopes),
                "status": status or self._chatgpt_status(credential),
                "provider_id": provider_id,
            },
        )

    def _has_same_chatgpt_tokens(
        self, row: Mapping[str, Any], credential: ChatGPTCredential
    ) -> bool:
        encrypted_tokens = (
            row.get("chatgpt_access_token_ciphertext"),
            row.get("chatgpt_refresh_token_ciphertext"),
            row.get("chatgpt_id_token_ciphertext"),
        )
        if not all(isinstance(token, str) and token for token in encrypted_tokens):
            return False
        access_ciphertext, refresh_ciphertext, id_token_ciphertext = encrypted_tokens
        return (
            hmac.compare_digest(self._decrypt(access_ciphertext), credential.access_token)
            and hmac.compare_digest(self._decrypt(refresh_ciphertext), credential.refresh_token)
            and hmac.compare_digest(self._decrypt(id_token_ciphertext), credential.id_token)
        )

    @staticmethod
    def _clear_chatgpt_tokens(cur: Any, provider_id: str, status: str) -> None:
        cur.execute(
            """
            UPDATE provider_connection
            SET chatgpt_access_token_ciphertext = NULL, chatgpt_refresh_token_ciphertext = NULL,
                chatgpt_id_token_ciphertext = NULL, chatgpt_access_token_expires_at = NULL,
                chatgpt_refresh_available_at = NULL, chatgpt_scopes = ARRAY[]::TEXT[],
                chatgpt_status = %s, revision = revision + 1, updated_at = now()
            WHERE id = %s
            """,
            (status, provider_id),
        )

    def import_chatgpt_credential(
        self,
        credential_payload: Mapping[str, Any],
        *,
        provider_id: str | None = None,
        name: str | None = None,
    ) -> dict[str, Any]:
        """Import an SSH-transferred record without adopting its source-machine host ID."""
        credential = validate_imported_credential(credential_payload)
        self.chatgpt_host_id()
        if name is not None and (not isinstance(name, str) or not name.strip()):
            raise ValueError("name must be a non-empty string")
        with self._pool.connection() as conn, conn.transaction(), conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(hashtext('provider_connection_default'))")
            target: dict[str, Any] | None = None
            if provider_id is not None:
                target = self._get_row(cur, provider_id, lock=True)
                if target["kind"] != "chatgpt":
                    raise ValueError("provider is not a ChatGPT plan OAuth provider")
                current_subject = target.get("chatgpt_subject")
                current_client = target.get("chatgpt_client_id")
                if current_subject is not None and (
                    current_subject != credential.subject or current_client != credential.client_id
                ):
                    raise ValueError("provider belongs to a different ChatGPT registration")
            else:
                cur.execute(
                    """
                    SELECT * FROM provider_connection
                    WHERE kind = 'chatgpt' AND chatgpt_issuer = %s
                      AND chatgpt_subject = %s AND chatgpt_client_id = %s
                    FOR UPDATE
                    """,
                    (credential.issuer, credential.subject, credential.client_id),
                )
                target = cur.fetchone()
            if target is None:
                base_name = (
                    name.strip()
                    if isinstance(name, str)
                    else (f"ChatGPT {credential.email}" if credential.email else "ChatGPT account")
                )
                candidate = base_name
                suffix = credential.client_id[-8:]
                cur.execute("SELECT 1 FROM provider_connection WHERE name = %s", (candidate,))
                if cur.fetchone() is not None:
                    candidate = f"{base_name} ({suffix})"
                cur.execute("SELECT 1 FROM provider_connection WHERE name = %s", (candidate,))
                if cur.fetchone() is not None:
                    raise ValueError("a provider with that name already exists")
                generated_id = str(uuid4())
                cur.execute("SELECT id FROM provider_connection WHERE is_default FOR UPDATE")
                has_default = cur.fetchone() is not None
                cur.execute(
                    """
                    INSERT INTO provider_connection
                        (id, name, kind, base_url, default_model, secret_source, is_default, chatgpt_status)
                    VALUES (%s, %s, 'chatgpt', %s, NULL, 'database', %s, 'unconnected')
                    RETURNING *
                    """,
                    (generated_id, candidate, CHATGPT_BASE_URL, not has_default),
                )
                target = cur.fetchone()
            assert target is not None
            current_saved_at = target.get("chatgpt_credentials_saved_at")
            if isinstance(current_saved_at, datetime):
                if self._has_same_chatgpt_tokens(target, credential):
                    return _row_public(target)
                if credential.saved_at <= current_saved_at:
                    raise ValueError("credential file predates the server's current token set")
            self._store_chatgpt_credential(cur, str(target["id"]), credential)
            updated = self._get_row(cur, str(target["id"]))
        return _row_public(updated)

    def _validate_pending_chatgpt_credential(
        self, provider_id: str, *, host_id: str
    ) -> ChatGPTCredential:
        """Validate a rotated ID token without consuming another refresh grant."""
        with self._pool.connection() as conn, conn.transaction(), conn.cursor() as cur:
            row = self._get_row(cur, provider_id, lock=True)
            if row["kind"] != "chatgpt":
                raise ValueError("provider is not a ChatGPT plan OAuth provider")
            credential = self._credential_from_row(
                row,
                host_id=host_id,
                allow_pending_validation=True,
            )
            if row.get("chatgpt_status") != _CHATGPT_PENDING_VALIDATION:
                return credential
            claims = validate_id_token(
                credential.id_token,
                client_id=credential.client_id,
                nonce=None,
            )
            if claims["sub"] != credential.subject:
                raise ValueError("ChatGPT refresh changed account identity")
            cur.execute(
                """
                UPDATE provider_connection
                SET chatgpt_status = %s, revision = revision + 1, updated_at = now()
                WHERE id = %s
                """,
                (self._chatgpt_status(credential), provider_id),
            )
        return credential

    def _chatgpt_access_token(
        self,
        provider_id: str,
        *,
        rejected_token: str | None = None,
    ) -> str:
        """Read, validate, or atomically rotate one registration's access token."""
        host_id = self.chatgpt_host_id()
        reauth_required = False
        access_token: str | None = None
        validate_pending = False
        with self._pool.connection() as conn, conn.transaction(), conn.cursor() as cur:
            row = self._get_row(cur, provider_id, lock=True)
            if row["kind"] != "chatgpt":
                raise ValueError("provider is not a ChatGPT plan OAuth provider")
            if row.get("chatgpt_status") == _CHATGPT_PENDING_VALIDATION:
                validate_pending = True
            else:
                credential = self._credential_from_row(row, host_id=host_id)
                if not credential.plan_enabled:
                    raise ValueError(_CHATGPT_PLAN_REAUTHORIZE_COMMAND)
                if rejected_token is not None and not hmac.compare_digest(
                    credential.access_token, rejected_token
                ):
                    return credential.access_token
                now = _utc_now()
                if (
                    rejected_token is None
                    and credential.access_token_expires_at > now + _CHATGPT_REFRESH_LEEWAY
                ):
                    return credential.access_token
                if (
                    credential.refresh_available_at is not None
                    and now < credential.refresh_available_at
                ):
                    raise ValueError("ChatGPT access token is expiring before it can be refreshed")
                try:
                    refreshed_payload = refresh_token_response(
                        client_id=credential.client_id,
                        refresh_token=credential.refresh_token,
                    )
                    id_token = refreshed_payload.get("id_token")
                    if id_token is not None and not isinstance(id_token, str):
                        raise ValueError("ChatGPT refresh returned an invalid ID token")
                    refreshed = credential_from_token_response(
                        refreshed_payload,
                        client_id=credential.client_id,
                        host_id=host_id,
                        claims={"sub": credential.subject, "email": credential.email},
                        previous=credential,
                    )
                except ChatGPTOAuthHttpError as exc:
                    if exc.code in _UNUSABLE_REFRESH_CODES:
                        self._clear_chatgpt_tokens(cur, provider_id, "reauth_required")
                        reauth_required = True
                    else:
                        raise ConnectionError("ChatGPT token refresh failed") from exc
                else:
                    if id_token is not None:
                        self._store_chatgpt_credential(
                            cur,
                            provider_id,
                            refreshed,
                            status=_CHATGPT_PENDING_VALIDATION,
                        )
                        validate_pending = True
                    else:
                        self._store_chatgpt_credential(cur, provider_id, refreshed)
                        access_token = refreshed.access_token
        if reauth_required:
            raise ValueError("ChatGPT account must sign in again")
        if validate_pending:
            credential = self._validate_pending_chatgpt_credential(provider_id, host_id=host_id)
            if not credential.plan_enabled:
                raise ValueError(_CHATGPT_PLAN_REAUTHORIZE_COMMAND)
            return credential.access_token
        assert access_token is not None
        return access_token

    def _refresh_chatgpt_access_token_after_rejection(
        self, provider_id: str, rejected_token: str
    ) -> str:
        """Refresh only if the rejected bearer remains the locked row's current token."""
        return self._chatgpt_access_token(provider_id, rejected_token=rejected_token)

    def disconnect_chatgpt(self, provider_id: str) -> dict[str, Any]:
        """Revoke the renewable session, then clear this registration's local tokens."""
        with self._pool.connection() as conn, conn.transaction(), conn.cursor() as cur:
            row = self._get_row(cur, provider_id, lock=True)
            if row["kind"] != "chatgpt":
                raise ValueError("provider is not a ChatGPT plan OAuth provider")
            refresh_ciphertext = row.get("chatgpt_refresh_token_ciphertext")
            client_id = row.get("chatgpt_client_id")
            if isinstance(refresh_ciphertext, str) and isinstance(client_id, str):
                refresh_token = self._decrypt(refresh_ciphertext)
                try:
                    revoke_refresh_token(client_id=client_id, refresh_token=refresh_token)
                except (ChatGPTOAuthHttpError, ConnectionError) as exc:
                    raise ConnectionError(
                        "ChatGPT session revocation could not be confirmed"
                    ) from exc
            self._clear_chatgpt_tokens(cur, provider_id, "disconnected")
            updated = self._get_row(cur, provider_id)
        return _row_public(updated)

    def resolve(
        self, provider_id: str | None = None, model_override: str | None = None
    ) -> dict[str, Any]:
        with self._pool.connection() as conn, conn.cursor() as cur:
            if provider_id is None:
                cur.execute("SELECT * FROM provider_connection WHERE is_default")
            else:
                cur.execute("SELECT * FROM provider_connection WHERE id = %s", (provider_id,))
            row = cur.fetchone()
        if row is None:
            raise LookupError("provider is not configured")
        kind = str(row["kind"])
        selected_model = model_override if model_override is not None else row.get("default_model")
        model = _normalize_model(selected_model, kind=kind)
        if kind == "chatgpt":
            api_key = self._chatgpt_access_token(str(row["id"]))
        elif row["secret_source"] == "environment":
            env_var = _allowed_environment_variable(row["env_var"])
            api_key = (os.getenv(env_var) or "").strip()
            if not api_key:
                raise ValueError("provider API key is not configured")
        else:
            api_key = self._decrypt(row["api_key_ciphertext"])
        return {
            "base_url": str(row["base_url"]),
            "api_key": api_key,
            "model_default": model,
            "provider_kind": kind,
            "provider_id": str(row["id"]),
            "revision": int(row["revision"]),
        }


def configure_provider_repository(database_url: str) -> ProviderRepository:
    """Open and install this process's provider repository singleton."""
    global _default_repository
    if _default_repository is not None:
        _default_repository.close()
        _default_repository = None
    repository = ProviderRepository(database_url)
    repository.open()
    _default_repository = repository
    return repository


def get_provider_repository() -> ProviderRepository:
    """Return the configured process-local repository."""
    if _default_repository is None:
        raise RuntimeError("provider repository is unavailable")
    return _default_repository


def shutdown_provider_repository() -> None:
    """Close and clear this process's provider repository singleton."""
    global _default_repository
    if _default_repository is not None:
        _default_repository.close()
        _default_repository = None


def _chatgpt_repository() -> ProviderRepository:
    return get_provider_repository()


async def refresh_chatgpt_access_token(provider_id: str) -> str:
    """Return a current ChatGPT bearer token at an outbound-call boundary."""
    repository = _chatgpt_repository()
    return await asyncio.to_thread(repository._chatgpt_access_token, provider_id)


def refresh_chatgpt_access_token_after_rejection_sync(provider_id: str, rejected_token: str) -> str:
    """Force a coordinated token refresh only when the rejected token is current."""
    repository = _chatgpt_repository()
    return repository._refresh_chatgpt_access_token_after_rejection(provider_id, rejected_token)


async def refresh_chatgpt_access_token_after_rejection(
    provider_id: str, rejected_token: str
) -> str:
    """Run a rejected-token refresh without blocking an async model call."""
    return await asyncio.to_thread(
        refresh_chatgpt_access_token_after_rejection_sync,
        provider_id,
        rejected_token,
    )


async def get_chatgpt_models(provider_id: str) -> list[dict[str, str]]:
    """Fetch the selected registration's currently visible ChatGPT model catalog."""
    repository = _chatgpt_repository()
    access_token = await asyncio.to_thread(repository._chatgpt_access_token, provider_id)
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
    timeout = aiohttp.ClientTimeout(total=20)
    try:
        async with (
            aiohttp.ClientSession(timeout=timeout) as session,
            session.get(f"{CHATGPT_BASE_URL}/models", headers=headers) as response,
        ):
            if response.status < 200 or response.status >= 300:
                raise ConnectionError(
                    f"ChatGPT model catalog request failed with HTTP {response.status}"
                )
            payload = await response.json(content_type=None)
    except aiohttp.ClientError as exc:
        raise ConnectionError("ChatGPT model catalog request failed") from exc
    models = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(models, list):
        raise ValueError("ChatGPT model catalog response is invalid")
    result: list[dict[str, str]] = []
    for model in models:
        if not isinstance(model, dict) or model.get("visibility") != "list":
            continue
        slug = model.get("slug")
        display_name = model.get("display_name")
        if isinstance(slug, str) and slug and isinstance(display_name, str) and display_name:
            result.append({"slug": slug, "display_name": display_name})
    return result


async def update_chatgpt_provider(
    provider_id: str,
    data: dict[str, Any],
    default_model: str | None,
) -> dict[str, Any]:
    """Validate an optional catalog model before atomically committing its patch."""
    if not isinstance(data, dict):
        raise ValueError("provider patch must be an object")
    selected: str | None = None
    if default_model is not None:
        if not isinstance(default_model, str) or not default_model.strip():
            raise ValueError("model must be a non-empty string")
        selected = default_model.strip()
        catalog = await get_chatgpt_models(provider_id)
        if selected not in {item["slug"] for item in catalog}:
            raise ValueError("model is not available to the selected ChatGPT account")
    repository = _chatgpt_repository()
    return await asyncio.to_thread(
        repository._update_chatgpt_provider,
        provider_id,
        dict(data),
        selected,
    )


async def test_chatgpt_connection(provider_id: str) -> None:
    """Run a supported streamed Responses request and require response.completed."""
    repository = _chatgpt_repository()
    connection = await asyncio.to_thread(repository.resolve, provider_id)
    if connection["provider_kind"] != "chatgpt":
        raise ValueError("provider is not a ChatGPT plan OAuth provider")
    headers = {
        "Authorization": f"Bearer {connection['api_key']}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    body = {
        "model": connection["model_default"],
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "Reply with OK."}]}],
        "store": False,
        "stream": True,
    }
    completed = False
    timeout = aiohttp.ClientTimeout(total=45)
    try:
        async with (
            aiohttp.ClientSession(timeout=timeout) as session,
            session.post(f"{CHATGPT_BASE_URL}/responses", headers=headers, json=body) as response,
        ):
            if response.status < 200 or response.status >= 300:
                raise ConnectionError(f"ChatGPT connection test failed with HTTP {response.status}")
            async for raw_line in response.content:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    continue
                try:
                    event = json.loads(data)
                except ValueError as exc:
                    raise ValueError("ChatGPT connection test returned an invalid stream") from exc
                if not isinstance(event, dict):
                    continue
                event_type = event.get("type")
                if event_type == "response.failed":
                    raise ValueError("ChatGPT connection test received a failed response")
                if event_type == "response.incomplete":
                    raise ValueError("ChatGPT connection test received an incomplete response")
                if event_type == "response.completed":
                    completed = True
    except aiohttp.ClientError as exc:
        raise ConnectionError("ChatGPT connection test failed") from exc
    if not completed:
        raise ValueError("ChatGPT connection test ended before response.completed")
