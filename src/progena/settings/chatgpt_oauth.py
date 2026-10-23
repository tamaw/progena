"""Sign in with ChatGPT OAuth protocol and protected local credential files.

This module is deliberately independent of the provider database.  The local CLI
owns the browser authorization flow; a remote Progena host receives only a file
transferred over a secure channel and imports it through the provider repository.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import stat
import tempfile
import threading
import webbrowser
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen
from uuid import uuid4

import jwt

CHATGPT_ISSUER = "https://auth.openai.com"
CHATGPT_RESOURCE = "https://api.openai.com/v1"
OPENID_CONFIGURATION_URL = f"{CHATGPT_ISSUER}/.well-known/openid-configuration"
DYNAMIC_CLIENT_ID = "dynamic_agent_client"
CHATGPT_PLAN_SCOPE = "chatgpt.tokens.use.direct"
REQUIRED_SCOPES = (
    "openid",
    "profile",
    "email",
    "offline_access",
    "resource.invoke",
    CHATGPT_PLAN_SCOPE,
)


class ChatGPTOAuthError(RuntimeError):
    """An OAuth operation failed without exposing an authorization artifact."""


class ChatGPTOAuthHttpError(ChatGPTOAuthError):
    """A safe representation of an OAuth endpoint response failure."""

    def __init__(self, status: int, code: str | None = None) -> None:
        self.status = status
        self.code = code
        detail = f"OAuth endpoint returned HTTP {status}"
        if code:
            detail = f"{detail} ({code})"
        super().__init__(detail)


def _required_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"credential {field} must be a non-empty string")
    return value.strip()


def _optional_string(value: object, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"credential {field} must be a string or null")
    return value.strip() or None


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _parse_timestamp(value: object, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"credential {field} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"credential {field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"credential {field} must include a timezone")
    return parsed.astimezone(UTC)


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _scopes(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        values = value.split()
    elif isinstance(value, list):
        values = value
    else:
        raise ValueError("credential scopes must be a list of strings")
    if not values or any(not isinstance(item, str) or not item.strip() for item in values):
        raise ValueError("credential scopes must contain non-empty strings")
    return tuple(dict.fromkeys(item.strip() for item in values))


@dataclass(frozen=True)
class ChatGPTCredential:
    """Validated local credential record; never serialize it to logs or APIs."""

    email: str | None
    issuer: str
    subject: str
    client_id: str
    ext_agent_host_id: str
    id_token: str
    access_token: str
    refresh_token: str
    token_type: str
    expires_in: int
    scopes: tuple[str, ...]
    saved_at: datetime
    access_token_expires_at: datetime
    refresh_available_at: datetime | None = None

    @property
    def plan_enabled(self) -> bool:
        return CHATGPT_PLAN_SCOPE in self.scopes

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> ChatGPTCredential:
        if not isinstance(payload, Mapping):
            raise ValueError("credential must be an object")
        issuer = _required_string(payload.get("issuer"), "issuer")
        if issuer != CHATGPT_ISSUER:
            raise ValueError("credential issuer is not OpenAI")
        token_type = _required_string(payload.get("token_type"), "token_type")
        if token_type.casefold() != "bearer":
            raise ValueError("credential token_type must be Bearer")
        expires_in = payload.get("expires_in")
        if isinstance(expires_in, bool) or not isinstance(expires_in, int) or expires_in <= 0:
            raise ValueError("credential expires_in must be a positive integer")
        saved_at = _parse_timestamp(payload.get("saved_at"), "saved_at")
        expires_value = payload.get("access_token_expires_at")
        expires_at = (
            _parse_timestamp(expires_value, "access_token_expires_at")
            if expires_value is not None
            else saved_at + timedelta(seconds=expires_in)
        )
        refresh_available_at = payload.get("refresh_available_at")
        return cls(
            email=_optional_string(payload.get("email"), "email"),
            issuer=issuer,
            subject=_required_string(payload.get("subject"), "subject"),
            client_id=_required_string(payload.get("client_id"), "client_id"),
            ext_agent_host_id=_required_string(
                payload.get("ext_agent_host_id"), "ext_agent_host_id"
            ),
            id_token=_required_string(payload.get("id_token"), "id_token"),
            access_token=_required_string(payload.get("access_token"), "access_token"),
            refresh_token=_required_string(payload.get("refresh_token"), "refresh_token"),
            token_type=token_type,
            expires_in=expires_in,
            scopes=_scopes(payload.get("scopes")),
            saved_at=saved_at,
            access_token_expires_at=expires_at,
            refresh_available_at=(
                _parse_timestamp(refresh_available_at, "refresh_available_at")
                if refresh_available_at is not None
                else None
            ),
        )

    def as_mapping(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "issuer": self.issuer,
            "subject": self.subject,
            "client_id": self.client_id,
            "ext_agent_host_id": self.ext_agent_host_id,
            "id_token": self.id_token,
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "token_type": self.token_type,
            "expires_in": self.expires_in,
            "scopes": list(self.scopes),
            "saved_at": _format_timestamp(self.saved_at),
            "access_token_expires_at": _format_timestamp(self.access_token_expires_at),
        }
        if self.email is not None:
            payload["email"] = self.email
        if self.refresh_available_at is not None:
            payload["refresh_available_at"] = _format_timestamp(self.refresh_available_at)
        return payload


def _request_json(
    url: str,
    *,
    form: Mapping[str, str] | None = None,
    timeout: float = 15.0,
    allow_empty_response: bool = False,
) -> dict[str, Any]:
    headers = {"Accept": "application/json"}
    body: bytes | None = None
    if form is not None:
        body = urlencode(form).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    request = Request(url, data=body, headers=headers, method="POST" if form is not None else "GET")
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310  # nosec B310 - fixed OpenAI metadata URLs
            raw = response.read()
    except HTTPError as exc:
        code: str | None = None
        with_context = exc.read()
        try:
            parsed = json.loads(with_context.decode("utf-8"))
            if isinstance(parsed, dict) and isinstance(parsed.get("error"), str):
                code = parsed["error"]
            elif isinstance(parsed, dict) and isinstance(parsed.get("error"), dict):
                candidate = parsed["error"].get("code")
                code = candidate if isinstance(candidate, str) else None
        except (UnicodeDecodeError, json.JSONDecodeError):
            pass
        raise ChatGPTOAuthHttpError(exc.code, code) from exc
    except (TimeoutError, URLError) as exc:
        raise ConnectionError("OAuth endpoint could not be reached") from exc
    if not raw and allow_empty_response:
        return {}
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ChatGPTOAuthError("OAuth endpoint returned an invalid response") from exc
    if not isinstance(payload, dict):
        raise ChatGPTOAuthError("OAuth endpoint returned an invalid response")
    return payload


def openid_configuration() -> dict[str, str]:
    """Return OpenAI discovery endpoints after validating their trusted origin."""
    payload = _request_json(OPENID_CONFIGURATION_URL)
    keys = ("issuer", "authorization_endpoint", "token_endpoint", "jwks_uri", "revocation_endpoint")
    result = {key: _required_string(payload.get(key), key) for key in keys}
    if result["issuer"] != CHATGPT_ISSUER:
        raise ChatGPTOAuthError("OpenID configuration issuer is invalid")
    for key in keys[1:]:
        parsed = urlsplit(result[key])
        if parsed.scheme != "https" or parsed.hostname != "auth.openai.com":
            raise ChatGPTOAuthError("OpenID configuration endpoint is invalid")
    return result


def validate_id_token(
    id_token: str,
    *,
    client_id: str,
    nonce: str | None,
    configuration: Mapping[str, str] | None = None,
    allow_expired: bool = False,
) -> dict[str, Any]:
    """Verify OpenAI's signed ID token and return only validated claims."""
    config = dict(configuration or openid_configuration())
    try:
        signing_key = jwt.PyJWKClient(config["jwks_uri"], timeout=15).get_signing_key_from_jwt(
            id_token
        )
        claims = jwt.decode(
            id_token,
            signing_key.key,
            algorithms=["RS256"],
            audience=client_id,
            issuer=CHATGPT_ISSUER,
            options={
                "require": ["exp", "iat", "sub", "iss", "aud"],
                "verify_exp": not allow_expired,
            },
        )
    except jwt.PyJWTError as exc:
        raise ChatGPTOAuthError("ID token validation failed") from exc
    if nonce is not None and not hmac.compare_digest(str(claims.get("nonce", "")), nonce):
        raise ChatGPTOAuthError("ID token nonce validation failed")
    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject:
        raise ChatGPTOAuthError("ID token has no subject")
    return claims


def _token_value(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ChatGPTOAuthError(f"token response omitted {key}")
    return value


def credential_from_token_response(
    payload: Mapping[str, Any],
    *,
    client_id: str,
    host_id: str,
    claims: Mapping[str, Any],
    previous: ChatGPTCredential | None = None,
) -> ChatGPTCredential:
    """Turn a trusted token response into one complete rotating credential record."""
    expires_in = payload.get("expires_in")
    if isinstance(expires_in, bool) or not isinstance(expires_in, int) or expires_in <= 0:
        raise ChatGPTOAuthError("token response has an invalid expires_in")
    saved_at = _utc_now()
    scope_value = payload.get("scope")
    scopes = _scopes(
        scope_value if scope_value is not None else list(previous.scopes) if previous else []
    )
    refresh_available_at: datetime | None = None
    earliest = payload.get("earliest_refresh_at")
    if isinstance(earliest, int) and not isinstance(earliest, bool):
        refresh_available_at = datetime.fromtimestamp(earliest, UTC)
    elif isinstance(earliest, str) and earliest:
        try:
            refresh_available_at = datetime.fromtimestamp(int(earliest), UTC)
        except ValueError as exc:
            raise ChatGPTOAuthError("token response has an invalid earliest_refresh_at") from exc
    email = claims.get("email")
    if email is not None and (not isinstance(email, str) or not email.strip()):
        raise ChatGPTOAuthError("ID token email claim is invalid")
    return ChatGPTCredential(
        email=email.strip() if isinstance(email, str) else (previous.email if previous else None),
        issuer=CHATGPT_ISSUER,
        subject=_required_string(claims.get("sub"), "subject"),
        client_id=client_id,
        ext_agent_host_id=host_id,
        id_token=(
            _token_value(payload, "id_token")
            if payload.get("id_token") is not None
            else previous.id_token
            if previous is not None
            else _token_value(payload, "id_token")
        ),
        access_token=_token_value(payload, "access_token"),
        refresh_token=_token_value(payload, "refresh_token"),
        token_type=_token_value(payload, "token_type"),
        expires_in=expires_in,
        scopes=scopes,
        saved_at=saved_at,
        access_token_expires_at=saved_at + timedelta(seconds=expires_in),
        refresh_available_at=refresh_available_at,
    )


def exchange_authorization_code(
    *,
    configuration: Mapping[str, str],
    client_id: str,
    code: str,
    code_verifier: str,
    redirect_uri: str,
) -> dict[str, Any]:
    """Exchange one callback code without ever putting it in a URL or log."""
    return _request_json(
        configuration["token_endpoint"],
        form={
            "grant_type": "authorization_code",
            "client_id": client_id,
            "code": code,
            "code_verifier": code_verifier,
            "redirect_uri": redirect_uri,
            "resource": CHATGPT_RESOURCE,
        },
    )


def refresh_token_response(*, client_id: str, refresh_token: str) -> dict[str, Any]:
    """Refresh a credential set with OpenAI's rotating refresh token endpoint."""
    configuration = openid_configuration()
    return _request_json(
        configuration["token_endpoint"],
        form={
            "grant_type": "refresh_token",
            "client_id": client_id,
            "refresh_token": refresh_token,
            "resource": CHATGPT_RESOURCE,
        },
    )


def revoke_refresh_token(*, client_id: str, refresh_token: str) -> None:
    """End the renewable session; callers retain credentials on any failure."""
    configuration = openid_configuration()
    _request_json(
        configuration["revocation_endpoint"],
        form={
            "token": refresh_token,
            "token_type_hint": "refresh_token",  # nosec B105 - RFC 7009 parameter value
            "client_id": client_id,
        },
        allow_empty_response=True,
    )


def _callback_server() -> tuple[ThreadingHTTPServer, threading.Event, dict[str, list[str]]]:
    received = threading.Event()
    result: dict[str, list[str]] = {}

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib handler hook
            parsed = urlsplit(self.path)
            if parsed.path != "/auth/callback":
                self.send_error(404)
                return
            if received.is_set():
                self.send_error(409)
                return
            result.update(parse_qs(parsed.query, keep_blank_values=True))
            received.set()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                b"<html><body><p>Sign-in complete. You may close this window.</p></body></html>"
            )

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), CallbackHandler)
    return server, received, result


def _first_callback_value(values: Mapping[str, list[str]], name: str) -> str | None:
    value = values.get(name)
    if value is None or len(value) != 1:
        return None
    return value[0]


def authorize_with_browser(
    *,
    host_id: str,
    agent_name: str,
    existing: ChatGPTCredential | None = None,
    enable_plan_usage: bool = False,
    timeout_seconds: float = 300.0,
) -> ChatGPTCredential:
    """Complete interactive PKCE/OIDC sign-in; plan recovery explicitly requests consent."""
    host_id = _required_string(host_id, "ext_agent_host_id")
    agent_name = _required_string(agent_name, "agent_name")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    configuration = openid_configuration()
    server, received, callback = _callback_server()
    listener = threading.Thread(target=server.serve_forever, daemon=True)
    listener.start()
    try:
        port = int(server.server_address[1])
        redirect_uri = f"http://127.0.0.1:{port}/auth/callback"
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode("ascii")).digest()
        ).rstrip(b"=")
        initial_registration = existing is None
        expected_client_id = DYNAMIC_CLIENT_ID if initial_registration else existing.client_id
        query: dict[str, str] = {
            "client_id": expected_client_id,
            "ext_agent_host_id": host_id,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "scope": " ".join(REQUIRED_SCOPES),
            "resource": CHATGPT_RESOURCE,
            "state": state,
            "nonce": nonce,
            "code_challenge_method": "S256",
            "code_challenge": challenge.decode("ascii"),
        }
        if initial_registration:
            query["agent_name_hint"] = agent_name
        else:
            query["id_token_hint"] = existing.id_token
            if existing.email:
                query["login_hint"] = existing.email
            if enable_plan_usage:
                query["prompt"] = "consent"
        authorization_url = f"{configuration['authorization_endpoint']}?{urlencode(query)}"
        if not webbrowser.open(authorization_url):
            raise ChatGPTOAuthError("could not open the system browser")
        if not received.wait(timeout_seconds):
            raise ChatGPTOAuthError("sign-in timed out waiting for the local callback")
        callback_state = _first_callback_value(callback, "state")
        if callback_state is None or not hmac.compare_digest(callback_state, state):
            raise ChatGPTOAuthError("OAuth callback state validation failed")
        callback_error = _first_callback_value(callback, "error")
        if callback_error is not None:
            raise ChatGPTOAuthError(f"OAuth authorization failed ({callback_error})")
        code = _first_callback_value(callback, "code")
        if not code:
            raise ChatGPTOAuthError("OAuth callback omitted an authorization code")
        callback_client_id = _first_callback_value(callback, "client_id")
        if initial_registration:
            if not callback_client_id or callback_client_id == DYNAMIC_CLIENT_ID:
                raise ChatGPTOAuthError("OAuth registration did not return an issued client ID")
            issued_client_id = callback_client_id
        else:
            if callback_client_id is not None and callback_client_id != existing.client_id:
                raise ChatGPTOAuthError(
                    "OAuth callback client ID does not match the selected account"
                )
            issued_client_id = existing.client_id
        token_response = exchange_authorization_code(
            configuration=configuration,
            client_id=issued_client_id,
            code=code,
            code_verifier=verifier,
            redirect_uri=redirect_uri,
        )
        id_token = _token_value(token_response, "id_token")
        claims = validate_id_token(
            id_token,
            client_id=issued_client_id,
            nonce=nonce,
            configuration=configuration,
        )
        if existing is not None and claims["sub"] != existing.subject:
            raise ChatGPTOAuthError("OAuth identity does not match the selected account")
        return credential_from_token_response(
            token_response,
            client_id=issued_client_id,
            host_id=host_id,
            claims=claims,
            previous=existing,
        )
    finally:
        server.shutdown()
        server.server_close()


def validate_imported_credential(payload: Mapping[str, Any]) -> ChatGPTCredential:
    """Revalidate an SSH-transferred record before the server stores its tokens."""
    credential = ChatGPTCredential.from_mapping(payload)
    claims = validate_id_token(
        credential.id_token,
        client_id=credential.client_id,
        nonce=None,
        allow_expired=True,
    )
    if claims["sub"] != credential.subject:
        raise ValueError("credential subject does not match its validated ID token")
    claim_email = claims.get("email")
    if credential.email is not None and claim_email != credential.email:
        raise ValueError("credential email does not match its validated ID token")
    return credential


def default_credential_path() -> Path:
    config_home = Path(os.getenv("XDG_CONFIG_HOME", Path.home() / ".config"))
    return config_home / "progena" / "chatgpt-credentials.json"


def default_host_id_path() -> Path:
    config_home = Path(os.getenv("XDG_CONFIG_HOME", Path.home() / ".config"))
    return config_home / "progena" / "chatgpt-host-id"


def _assert_protected_file(path: Path) -> None:
    if path.is_symlink():
        raise ValueError("credential path must not be a symbolic link")
    details = path.stat()
    if not stat.S_ISREG(details.st_mode):
        raise ValueError("credential path must be a regular file")
    if details.st_mode & 0o077:
        raise ValueError("credential file permissions must be owner-only")
    if hasattr(os, "getuid") and details.st_uid != os.getuid():
        raise ValueError("credential file must be owned by the current user")


def load_protected_credential(path: Path) -> ChatGPTCredential:
    """Read one owner-only credential file without changing it."""
    _assert_protected_file(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("credential file is invalid") from exc
    if not isinstance(payload, dict):
        raise ValueError("credential file must contain an object")
    return ChatGPTCredential.from_mapping(payload)


def write_protected_credential(path: Path, credential: ChatGPTCredential) -> None:
    """Atomically replace a credential record with owner-only Unix permissions."""
    parent = path.expanduser().parent
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    destination = path.expanduser()
    contents = json.dumps(credential.as_mapping(), separators=(",", ":"), sort_keys=True).encode(
        "utf-8"
    )
    descriptor, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", dir=parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(contents)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        directory_descriptor = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        with suppress(FileNotFoundError):
            os.unlink(temporary)


def load_or_create_host_id(path: Path | None = None) -> str:
    """Persist a stable opaque host ID for the local browser-running machine."""
    target = (path or default_host_id_path()).expanduser()
    if target.exists():
        _assert_protected_file(target)
        host_id = target.read_text(encoding="utf-8").strip()
        if host_id:
            return host_id
        raise ValueError("host ID file is empty")
    parent = target.parent
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    host_id = f"urn:uuid:{uuid4()}"
    descriptor, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = -1
            handle.write(host_id + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, target)
        except FileExistsError:
            _assert_protected_file(target)
            imported_host_id = target.read_text(encoding="utf-8").strip()
            if imported_host_id:
                return imported_host_id
            raise ValueError("host ID file is empty")
        directory_descriptor = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        with suppress(FileNotFoundError):
            os.unlink(temporary)
    return host_id
