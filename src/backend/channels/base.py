"""Channel type descriptors and the adapter contract every channel implements.

A channel *type* (for example Telegram) declares its display metadata,
capabilities, non-secret settings and credentials in a :class:`ChannelTypeSpec`
and runs through a :class:`ChannelAdapter`. Documents (``channel:<id>``) select
a type and hold settings; :mod:`src.backend.channels.manager` turns documents
into running adapters. Adding a channel type means adding an adapter module
and registering it in :mod:`src.backend.channels.registry`.
"""

from __future__ import annotations

import math
import re
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Mapping
from dataclasses import dataclass, field
from typing import Any, ClassVar, Literal, Protocol

SettingKind = Literal["text", "integer", "number", "boolean", "list"]
ChannelState = Literal[
    "connected", "connecting", "disconnected", "error", "disabled", "unconfigured", "unsupported"
]
_ENV_VAR_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")
# Any channel credential may live in a dedicated variable with this prefix, in
# addition to each credential's own conventional variable.
CHANNEL_ENV_VAR_PREFIX = "PROGENA_CHANNEL_"


class ChannelError(RuntimeError):
    """A channel operation failed with a user-presentable reason."""


@dataclass(frozen=True)
class SettingField:
    """One non-secret setting of a channel type."""

    key: str
    label: str
    kind: SettingKind
    default: Any
    help: str = ""
    required: bool = False
    advanced: bool = False
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    placeholder: str | None = None

    def public(self) -> dict[str, Any]:
        public: dict[str, Any] = {
            "key": self.key,
            "label": self.label,
            "kind": self.kind,
            "help": self.help,
            "required": self.required,
            "advanced": self.advanced,
        }
        for name, value in (("min", self.minimum), ("max", self.maximum), ("step", self.step)):
            if value is not None:
                public[name] = value
        if self.placeholder is not None:
            public["placeholder"] = self.placeholder
        return public

    def validate(self, value: Any) -> Any:
        """Return ``value`` normalized for this field, else raise ``ValueError``."""
        label = f"settings.{self.key}"
        if self.kind == "text":
            if not isinstance(value, str):
                raise ValueError(f"{label} must be a string")
            value = value.strip()
            if self.required and not value:
                raise ValueError(f"{label} is required")
            return value
        if self.kind == "boolean":
            if not isinstance(value, bool):
                raise ValueError(f"{label} must be a boolean")
            return value
        if self.kind == "list":
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                raise ValueError(f"{label} must be a list of strings")
            items: list[str] = []
            for item in (item.strip() for item in value):
                if item and item not in items:
                    items.append(item)
            return items
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ValueError(f"{label} must be a number")
        if self.kind == "integer":
            if isinstance(value, float) and not value.is_integer():
                raise ValueError(f"{label} must be an integer")
            value = int(value)
        else:
            value = float(value)
            if not math.isfinite(value):
                raise ValueError(f"{label} must be finite")
        if self.minimum is not None and value < self.minimum:
            raise ValueError(f"{label} must be at least {self.minimum:g}")
        if self.maximum is not None and value > self.maximum:
            raise ValueError(f"{label} must be at most {self.maximum:g}")
        return value


@dataclass(frozen=True)
class CredentialField:
    """One secret a channel type needs, with its conventional variable."""

    key: str
    label: str
    default_env_var: str
    help: str = ""

    def validate_env_var(self, value: Any) -> str:
        """Allow the conventional variable or a ``PROGENA_CHANNEL_*`` one."""
        name = value.strip() if isinstance(value, str) else ""
        if _ENV_VAR_RE.match(name) and (
            name == self.default_env_var
            or (name.startswith(CHANNEL_ENV_VAR_PREFIX) and name != CHANNEL_ENV_VAR_PREFIX)
        ):
            return name
        raise ValueError(
            f"credentials.{self.key}.env_var must be {self.default_env_var} "
            f"or a {CHANNEL_ENV_VAR_PREFIX}* variable"
        )


@dataclass(frozen=True)
class ChannelCapabilities:
    inbound: bool
    outbound: bool
    streaming: bool
    threads: bool
    attachments: bool

    def public(self) -> dict[str, bool]:
        return {
            "inbound": self.inbound,
            "outbound": self.outbound,
            "streaming": self.streaming,
            "threads": self.threads,
            "attachments": self.attachments,
        }


@dataclass(frozen=True)
class ChannelTypeSpec:
    """Everything the backend and UI need to know about one channel type."""

    type: str
    name: str
    category: str
    description: str
    # The chat session ``source`` conversations through this channel carry.
    session_source: str
    capabilities: ChannelCapabilities
    fields: tuple[SettingField, ...] = ()
    credentials: tuple[CredentialField, ...] = ()
    _fields_by_key: dict[str, SettingField] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_fields_by_key", {item.key: item for item in self.fields})

    def default_settings(self) -> dict[str, Any]:
        return {item.key: item.default for item in self.fields}

    def credential(self, key: str) -> CredentialField:
        for item in self.credentials:
            if item.key == key:
                return item
        raise ValueError(f"credentials.{key} is not a {self.name} credential")

    def validate_settings(self, settings: Any, *, partial: bool = False) -> dict[str, Any]:
        """Validate settings; a full set is completed from defaults.

        Unknown keys are rejected so a typo never silently does nothing.
        """
        if not isinstance(settings, Mapping):
            raise ValueError("settings must be an object")
        unknown = sorted(str(key) for key in settings if key not in self._fields_by_key)
        if unknown:
            raise ValueError(f"Unknown {self.name} settings: {', '.join(unknown)}")
        validated = {
            key: self._fields_by_key[key].validate(value) for key, value in settings.items()
        }
        if partial:
            return validated
        return {**self.default_settings(), **validated}

    def missing_requirements(self, settings: Mapping[str, Any]) -> list[str]:
        """Labels of required settings that are still empty."""
        return [
            item.label
            for item in self.fields
            if item.required and item.kind == "text" and not str(settings.get(item.key) or "")
        ]

    def lenient_settings(self, settings: Mapping[str, Any]) -> dict[str, Any]:
        """Defaults overlaid with each valid known value; invalid values are dropped.

        For seeding from legacy configuration, where one bad value must not
        prevent the channel document from being created.
        """
        values = self.default_settings()
        for key, value in settings.items():
            item = self._fields_by_key.get(key)
            if item is None:
                continue
            try:
                values[key] = item.validate(value)
            except ValueError:
                continue
        return values


@dataclass(frozen=True)
class AdapterStatus:
    state: ChannelState
    detail: str | None = None
    handle: str | None = None


class ChannelHost(Protocol):
    """What the manager offers a running adapter."""

    def runtime_client(self) -> Any | None:
        """The Progena runtime client, when the backend has one."""
        ...

    def document_store(self) -> Any:
        """The backend document store."""
        ...

    def status_changed(self, channel_id: str) -> None:
        """Report a connection-state change outside a request."""
        ...

    def record_message(self, channel_id: str, direction: Literal["inbound", "outbound"]) -> None:
        """Count one message received from or sent to the channel."""
        ...

    def spawn(self, coroutine: Awaitable[Any]) -> None:
        """Run a background coroutine owned by the backend."""
        ...


class ChannelAdapter(ABC):
    """A running channel: one instance per enabled ``channel:<id>`` document."""

    spec: ClassVar[ChannelTypeSpec]

    def __init__(
        self,
        channel_id: str,
        settings: Mapping[str, Any],
        secrets: Mapping[str, str],
        host: ChannelHost,
    ) -> None:
        self.channel_id = channel_id
        self.settings = dict(settings)
        self.secrets = dict(secrets)
        self.host = host

    @classmethod
    @abstractmethod
    def seed_from_environment(cls) -> tuple[bool, dict[str, Any], dict[str, Any]]:
        """``(enabled, settings, credential references)`` for a first document."""

    @abstractmethod
    async def start(self) -> None:
        """Connect; failures are reported through :meth:`status`, not raised."""

    @abstractmethod
    async def stop(self) -> None:
        """Disconnect and release resources."""

    @abstractmethod
    def status(self) -> AdapterStatus:
        """Current connection state."""

    @abstractmethod
    async def send_test(self) -> None:
        """Send a test message; raise :class:`ChannelError` on failure."""

    @abstractmethod
    async def notify(self, content: str, event_type: str) -> bool:
        """Deliver an agent message while the user is away from the web UI."""

    def notifies_when_away(self) -> bool:
        return bool(self.settings.get("notify_when_away", False))
