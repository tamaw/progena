"""Persisted Responses API provider connections shared by backend and runtime."""

from __future__ import annotations

import os
from collections.abc import Mapping
from contextlib import suppress
from typing import Any
from urllib.parse import parse_qsl, urlsplit, urlunsplit
from uuid import uuid4

import psycopg
from cryptography.fernet import Fernet, InvalidToken
from psycopg import errors, sql
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

GO_RESPONSES_MODELS = (
    "grok-4.7",
    "gpt-6-luna",
    "gpt-5.6-luna",
    "muse-spark-1.3-contributor",
    "muse-spark-1.2-contributor",
)
GO_BASE_URL = "https://opencode.ai/inference/go/openai/v1"

_PROVIDER_KINDS = frozenset({"foundry", "opencode_go"})
_SECRET_SOURCES = frozenset({"database", "environment"})
_AZURE_HOST_SUFFIXES = (
    ".openai.azure.com",
    ".services.ai.azure.com",
    ".cognitiveservices.azure.com",
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


def _row_public(row: Mapping[str, Any]) -> dict[str, Any]:
    source = str(row["secret_source"])
    env_var = row.get("env_var")
    has_api_key = bool(row.get("api_key_ciphertext"))
    if source == "environment" and isinstance(env_var, str):
        has_api_key = bool(os.getenv(env_var))
    return {
        "id": str(row["id"]),
        "name": str(row["name"]),
        "kind": str(row["kind"]),
        "base_url": str(row["base_url"]),
        "default_model": str(row["default_model"]),
        "secret_source": source,
        "env_var": env_var if source == "environment" else None,
        "has_api_key": has_api_key,
        "is_default": bool(row["is_default"]),
        "revision": int(row["revision"]),
    }


class ProviderRepository:
    """Synchronous Postgres repository for connection settings and encrypted keys."""

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
                    kind TEXT NOT NULL CHECK (kind IN ('foundry', 'opencode_go')),
                    base_url TEXT NOT NULL,
                    default_model TEXT NOT NULL,
                    secret_source TEXT NOT NULL CHECK (secret_source IN ('database', 'environment')),
                    env_var TEXT,
                    api_key_ciphertext TEXT,
                    is_default BOOLEAN NOT NULL DEFAULT FALSE,
                    revision INTEGER NOT NULL DEFAULT 1,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    CHECK ((secret_source = 'environment' AND env_var IS NOT NULL) OR secret_source = 'database')
                )
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
            raise ValueError("kind must be 'foundry' or 'opencode_go'")
        name_value = data.get("name", existing["name"] if existing else "")
        if not isinstance(name_value, str) or not name_value.strip():
            raise ValueError("name must be a non-empty string")
        name = name_value.strip()
        if kind == "opencode_go":
            supplied_base = data.get("base_url", existing["base_url"] if existing else GO_BASE_URL)
            if supplied_base not in (None, "", GO_BASE_URL):
                raise ValueError("base_url is fixed for OpenCode Go")
            base_url = GO_BASE_URL
        else:
            base_url = _normalize_foundry_base_url(
                data.get("base_url", existing["base_url"] if existing else None)
            )
        default_model = _normalize_model(
            data.get("default_model", existing["default_model"] if existing else None), kind=kind
        )
        source = str(
            data.get("secret_source", existing["secret_source"] if existing else "database")
        ).strip()
        if source not in _SECRET_SOURCES:
            raise ValueError("secret_source must be 'database' or 'environment'")
        env_var: str | None = None
        if source == "environment":
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
        if fields["secret_source"] == "database":
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
                    (id, name, kind, base_url, default_model, secret_source, env_var, api_key_ciphertext, is_default)
                VALUES (%(id)s, %(name)s, %(kind)s, %(base_url)s, %(default_model)s, %(secret_source)s,
                        %(env_var)s, %(api_key_ciphertext)s, %(is_default)s)
                RETURNING *
                """,
                {
                    **fields,
                    "id": provider_id,
                    "api_key_ciphertext": ciphertext,
                    "is_default": is_default,
                },
            )
            row = cur.fetchone()
        assert row is not None
        return _row_public(row)

    def update(self, provider_id: str, data: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(data, dict) or not data:
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
            api_key = data.get("api_key", ...)
            ciphertext = existing.get("api_key_ciphertext")
            if fields["secret_source"] == "database":
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

    def delete(self, provider_id: str) -> None:
        with self._pool.connection() as conn, conn.transaction(), conn.cursor() as cur:
            row = self._get_row(cur, provider_id, lock=True)
            if row["is_default"]:
                raise ValueError("default provider must be switched before removal")
            cur.execute("DELETE FROM provider_connection WHERE id = %s", (provider_id,))

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
        model = (
            _normalize_model(model_override, kind=kind)
            if model_override is not None
            else str(row["default_model"])
        )
        if row["secret_source"] == "environment":
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
