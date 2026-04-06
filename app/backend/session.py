"""
Session Management Module - User session state persistence and restoration.

Based on patterns from text-generation-webui for state persistence.

Features:
- Session state persistence across reloads
- Dark/light theme preferences
- Extension/feature toggles
- Settings export/import (YAML)
- Multi-user session isolation
- Session timeout and cleanup
"""

import asyncio
import contextlib
import json
import logging
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, cast

import aiofiles
import yaml
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class SessionConfig(BaseModel):
    """Configuration for session management."""

    session_timeout_minutes: int = 60 * 24  # 24 hours
    max_sessions: int = 1000
    cleanup_interval_seconds: int = 300  # 5 minutes
    persist_dir: Path | None = None
    cookie_name: str = "progenitor_session"
    cookie_max_age: int = 60 * 60 * 24 * 7  # 7 days


class UserPreferences(BaseModel):
    """User preferences that persist across sessions."""

    # UI preferences
    theme: str = "dark"  # dark, light
    font_size: str = "medium"  # small, medium, large
    show_timestamps: bool = True
    show_token_counts: bool = False

    # Chat preferences
    stream_responses: bool = True
    auto_scroll: bool = True
    show_thinking: bool = False  # Show reasoning/thinking content

    # Model preferences
    default_model: str = "progenitor-v1"
    temperature: float = 0.7
    max_tokens: int | None = None

    # Feature toggles
    enabled_features: list[str] = Field(
        default_factory=lambda: [
            "attachments",
            "web_search",
            "code_highlighting",
        ]
    )

    # Custom settings
    custom: dict[str, Any] = Field(default_factory=dict)


class SessionState(BaseModel):
    """Full session state."""

    # Identity
    session_id: str = Field(default_factory=lambda: f"sess_{uuid.uuid4().hex[:16]}")
    user_id: str | None = None  # For authenticated users
    created_at: datetime = Field(default_factory=datetime.utcnow)
    last_activity: datetime = Field(default_factory=datetime.utcnow)

    # Preferences
    preferences: UserPreferences = Field(default_factory=UserPreferences)

    # Conversation state
    conversation_id: str | None = None
    message_count: int = 0

    # Temporary state
    current_model: str | None = None
    pending_attachments: list[str] = Field(default_factory=list)  # Attachment IDs

    # Extension state
    extension_states: dict[str, dict[str, Any]] = Field(default_factory=dict)

    # Flags
    is_authenticated: bool = False
    is_admin: bool = False

    def touch(self) -> None:
        """Update last activity timestamp."""
        self.last_activity = datetime.utcnow()

    def is_expired(self, timeout_minutes: int) -> bool:
        """Check if session has expired."""
        expiry = self.last_activity + timedelta(minutes=timeout_minutes)
        return datetime.utcnow() > expiry


class SessionManager:
    """
    Manages user sessions with persistence.

    Provides:
    - Session creation and retrieval
    - Preference persistence
    - Session cleanup
    - Multi-user isolation
    """

    def __init__(self, config: SessionConfig | None = None):
        self.config = config or SessionConfig()
        self._sessions: dict[str, SessionState] = {}
        self._user_sessions: dict[str, str] = {}  # user_id -> session_id
        self._cleanup_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

        # Persistence directory
        if self.config.persist_dir:
            self.config.persist_dir.mkdir(parents=True, exist_ok=True)

    async def start(self) -> None:
        """Start the session manager."""
        # Load persisted sessions
        if self.config.persist_dir:
            await self._load_sessions()

        # Start cleanup task
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        logger.info("Session manager started")

    async def stop(self) -> None:
        """Stop the session manager."""
        # Cancel cleanup task
        if self._cleanup_task:
            self._cleanup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._cleanup_task

        # Persist sessions
        if self.config.persist_dir:
            await self._save_sessions()

        logger.info("Session manager stopped")

    async def create_session(
        self,
        user_id: str | None = None,
        preferences: UserPreferences | None = None,
    ) -> SessionState:
        """Create a new session."""
        async with self._lock:
            # Check capacity
            if len(self._sessions) >= self.config.max_sessions:
                await self._evict_oldest_sessions(self.config.max_sessions // 10)

            # Check for existing user session
            if user_id and user_id in self._user_sessions:
                existing_id = self._user_sessions[user_id]
                if existing_id in self._sessions:
                    session = self._sessions[existing_id]
                    session.touch()
                    return session

            # Create new session
            session = SessionState(
                user_id=user_id,
                preferences=preferences or UserPreferences(),
                is_authenticated=user_id is not None,
            )

            self._sessions[session.session_id] = session
            if user_id:
                self._user_sessions[user_id] = session.session_id

            logger.debug("Created session: %s (user=%s)", session.session_id, user_id)
            return session

    async def get_session(
        self,
        session_id: str,
        touch: bool = True,
    ) -> SessionState | None:
        """Get session by ID."""
        session = self._sessions.get(session_id)

        if session:
            if session.is_expired(self.config.session_timeout_minutes):
                await self.delete_session(session_id)
                return None

            if touch:
                session.touch()

        return session

    async def get_or_create_session(
        self,
        session_id: str | None = None,
        user_id: str | None = None,
    ) -> SessionState:
        """Get existing session or create new one."""
        if session_id:
            session = await self.get_session(session_id)
            if session:
                return session

        return await self.create_session(user_id=user_id)

    async def update_preferences(
        self,
        session_id: str,
        **preferences,
    ) -> SessionState | None:
        """Update session preferences."""
        session = await self.get_session(session_id)
        if not session:
            return None

        for key, value in preferences.items():
            if hasattr(session.preferences, key):
                setattr(session.preferences, key, value)
            else:
                session.preferences.custom[key] = value

        return session

    async def delete_session(self, session_id: str) -> bool:
        """Delete a session."""
        async with self._lock:
            session = self._sessions.pop(session_id, None)
            if session:
                if session.user_id and session.user_id in self._user_sessions:
                    del self._user_sessions[session.user_id]
                logger.debug("Deleted session: %s", session_id)
                return True
            return False

    async def get_session_by_user(self, user_id: str) -> SessionState | None:
        """Get session by user ID."""
        session_id = self._user_sessions.get(user_id)
        if session_id:
            return await self.get_session(session_id)
        return None

    async def _evict_oldest_sessions(self, count: int) -> int:
        """Evict oldest sessions."""
        if not self._sessions:
            return 0

        # Sort by last activity
        sorted_sessions = sorted(
            self._sessions.items(),
            key=lambda x: x[1].last_activity,
        )

        evicted = 0
        for session_id, session in sorted_sessions[:count]:
            self._sessions.pop(session_id, None)
            if session.user_id:
                self._user_sessions.pop(session.user_id, None)
            evicted += 1

        if evicted:
            logger.info("Evicted %d oldest sessions", evicted)

        return evicted

    async def _cleanup_expired(self) -> int:
        """Clean up expired sessions."""
        async with self._lock:
            expired = []
            for session_id, session in self._sessions.items():
                if session.is_expired(self.config.session_timeout_minutes):
                    expired.append(session_id)

            for session_id in expired:
                expired_session = self._sessions.get(session_id)
                if expired_session is not None:
                    del self._sessions[session_id]
                if expired_session and expired_session.user_id:
                    self._user_sessions.pop(expired_session.user_id, "")

            if expired:
                logger.debug("Cleaned up %d expired sessions", len(expired))

            return len(expired)

    async def _cleanup_loop(self) -> None:
        """Background cleanup loop."""
        while True:
            try:
                await asyncio.sleep(self.config.cleanup_interval_seconds)
                await self._cleanup_expired()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Session cleanup error: %s", e)

    async def _save_sessions(self) -> None:
        """Persist sessions to disk."""
        if not self.config.persist_dir:
            return

        sessions_file = self.config.persist_dir / "sessions.json"
        data = {
            session_id: session.model_dump(mode="json")
            for session_id, session in self._sessions.items()
        }

        try:
            async with aiofiles.open(sessions_file, "w") as f:
                await f.write(json.dumps(data, indent=2, default=str))
            logger.debug("Saved %d sessions", len(data))
        except Exception as e:
            logger.error("Failed to save sessions: %s", e)

    async def _load_sessions(self) -> None:
        """Load persisted sessions."""
        if not self.config.persist_dir:
            return

        sessions_file = self.config.persist_dir / "sessions.json"
        if not sessions_file.exists():
            return

        try:
            async with aiofiles.open(sessions_file) as f:
                data = json.loads(await f.read())

            loaded = 0
            for session_id, session_data in data.items():
                try:
                    session = SessionState.model_validate(session_data)
                    if not session.is_expired(self.config.session_timeout_minutes):
                        self._sessions[session_id] = session
                        if session.user_id:
                            self._user_sessions[session.user_id] = session_id
                        loaded += 1
                except Exception as e:
                    logger.warning("Failed to load session %s: %s", session_id, e)

            logger.info("Loaded %d sessions from disk", loaded)

        except Exception as e:
            logger.error("Failed to load sessions: %s", e)

    def get_stats(self) -> dict[str, Any]:
        """Get session statistics."""
        now = datetime.utcnow()
        active_count = sum(
            1
            for s in self._sessions.values()
            if (now - s.last_activity).total_seconds() < 300  # Active in last 5 min
        )

        return {
            "total_sessions": len(self._sessions),
            "authenticated_sessions": sum(1 for s in self._sessions.values() if s.is_authenticated),
            "active_sessions": active_count,
            "max_sessions": self.config.max_sessions,
        }


class SettingsManager:
    """
    Manages settings export/import in YAML format.

    Compatible with text-generation-webui settings format.
    """

    @staticmethod
    def export_settings(
        preferences: UserPreferences,
        extension_states: dict[str, dict[str, Any]] | None = None,
    ) -> str:
        """Export settings to YAML string."""
        settings = {
            "theme": preferences.theme,
            "font_size": preferences.font_size,
            "show_timestamps": preferences.show_timestamps,
            "show_token_counts": preferences.show_token_counts,
            "stream_responses": preferences.stream_responses,
            "auto_scroll": preferences.auto_scroll,
            "show_thinking": preferences.show_thinking,
            "default_model": preferences.default_model,
            "temperature": preferences.temperature,
            "enabled_features": preferences.enabled_features,
        }

        if preferences.max_tokens:
            settings["max_tokens"] = preferences.max_tokens

        if preferences.custom:
            settings.update(preferences.custom)

        if extension_states:
            for ext_name, ext_state in extension_states.items():
                for key, value in ext_state.items():
                    settings[f"{ext_name}-{key}"] = value

        return cast(str, yaml.dump(settings, default_flow_style=False, sort_keys=False))

    @staticmethod
    def import_settings(yaml_content: str) -> tuple[UserPreferences, dict[str, dict[str, Any]]]:
        """Import settings from YAML string."""
        settings = yaml.safe_load(yaml_content) or {}

        # Extract preferences
        prefs = UserPreferences(
            theme=settings.get("theme", "dark"),
            font_size=settings.get("font_size", "medium"),
            show_timestamps=settings.get("show_timestamps", True),
            show_token_counts=settings.get("show_token_counts", False),
            stream_responses=settings.get("stream_responses", True),
            auto_scroll=settings.get("auto_scroll", True),
            show_thinking=settings.get("show_thinking", False),
            default_model=settings.get("default_model", "progenitor-v1"),
            temperature=settings.get("temperature", 0.7),
            max_tokens=settings.get("max_tokens"),
            enabled_features=settings.get(
                "enabled_features", ["attachments", "web_search", "code_highlighting"]
            ),
        )

        # Extract extension states (keys with "-")
        extension_states: dict[str, dict[str, Any]] = {}
        for key, value in settings.items():
            if "-" in key and not key.startswith("_"):
                parts = key.split("-", 1)
                ext_name, ext_key = parts
                if ext_name not in extension_states:
                    extension_states[ext_name] = {}
                extension_states[ext_name][ext_key] = value

        # Custom settings (not standard prefs or extension)
        known_keys = {
            "theme",
            "font_size",
            "show_timestamps",
            "show_token_counts",
            "stream_responses",
            "auto_scroll",
            "show_thinking",
            "default_model",
            "temperature",
            "max_tokens",
            "enabled_features",
        }
        for key, value in settings.items():
            if key not in known_keys and "-" not in key:
                prefs.custom[key] = value

        return prefs, extension_states

    @staticmethod
    async def save_to_file(
        filepath: Path,
        preferences: UserPreferences,
        extension_states: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        """Save settings to YAML file."""
        content = SettingsManager.export_settings(preferences, extension_states)
        async with aiofiles.open(filepath, "w") as f:
            await f.write(content)

    @staticmethod
    async def load_from_file(
        filepath: Path,
    ) -> tuple[UserPreferences, dict[str, dict[str, Any]]]:
        """Load settings from YAML file."""
        async with aiofiles.open(filepath) as f:
            content = await f.read()
        return SettingsManager.import_settings(content)


# FastAPI middleware helpers


async def get_session_from_request(
    request,
    session_manager: SessionManager,
    cookie_name: str = "progenitor_session",
) -> SessionState:
    """Extract or create session from FastAPI request."""
    session_id = request.cookies.get(cookie_name)
    user_id = getattr(request.state, "user_id", None)  # From auth middleware

    return await session_manager.get_or_create_session(
        session_id=session_id,
        user_id=user_id,
    )


def set_session_cookie(
    response,
    session: SessionState,
    cookie_name: str = "progenitor_session",
    max_age: int = 60 * 60 * 24 * 7,  # 7 days
) -> None:
    """Set session cookie on FastAPI response."""
    response.set_cookie(
        key=cookie_name,
        value=session.session_id,
        max_age=max_age,
        httponly=True,
        samesite="lax",
    )
