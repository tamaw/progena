"""Strict A2A reply contracts and sanitized correction diagnostics.

An A2A node that declares ``response_schema`` requires the peer's completed
reply to be exactly one JSON object validated against a local JSON Schema.
Unlike :func:`messages.extract_json_object`, nothing around the object is
tolerated. Diagnostics carry codes, bounded paths and schema-side constraints
only: offending values, reply-side keys and validator messages are never
reflected, because they are untrusted peer output that would otherwise be
quoted back into a privileged correction instruction.

See docs/a2a-response-contracts.md for the full contract.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

MAX_CORRECTION_ATTEMPTS = 3
_MAX_DIAGNOSTICS = 20
_MAX_PATH_SEGMENTS = 8
_MAX_LISTED_NAMES = 32
# Schema-side names/enum values are echoed only when they look like plain
# identifiers; anything else (spaces, markup, long text) is omitted.
_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.\-]{0,63}$")
_JSON_WHITESPACE = " \t\n\r"
_REF_KEYWORDS = ("$ref", "$dynamicRef", "$recursiveRef")
_DEFS_PREFIX = "#/$defs/"
_DECLINE_SENTINEL = "CONTRACT_CORRECTION_UNAVAILABLE"
_RANGE_KEYWORDS = frozenset(
    {
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
        "minLength",
        "maxLength",
        "minItems",
        "maxItems",
        "minProperties",
        "maxProperties",
        "minContains",
        "maxContains",
    }
)
_KNOWN_KEYWORDS = frozenset(
    {
        "type",
        "enum",
        "const",
        "required",
        "additionalProperties",
        "unevaluatedProperties",
        "additionalItems",
        "unevaluatedItems",
        "pattern",
        "format",
        "anyOf",
        "oneOf",
        "not",
        "dependentRequired",
        "dependencies",
        "uniqueItems",
        "contains",
        "propertyNames",
        "false",
        *_RANGE_KEYWORDS,
    }
)

CORRECTION_INSTRUCTIONS = (
    "Your previous reply in this conversation did not satisfy the response contract "
    "required by the caller. This is a formatting correction request only.\n"
    "Rules:\n"
    "- Reply again with the SAME feedback you already gave, reformatted or completed "
    "only from information already present in your previous reply.\n"
    "- Preserve every factual decision from your previous reply.\n"
    "- Do not perform new research, call tools, place or modify orders, send messages, "
    "or repeat any work or action.\n"
    "- Do not invent values. If the contract cannot be satisfied without new facts or "
    f"new work, reply with exactly {_DECLINE_SENTINEL} and nothing else.\n"
    "- Otherwise reply with exactly one JSON object and nothing else: no Markdown code "
    "fences, no prose, no comments.\n"
    "Validation problems found by the caller (codes, field paths and expected "
    "constraints only):\n"
)


class A2AContractConfigError(ValueError):
    """The declared contract cannot be enforced (bad schema or no validator)."""


class A2AContractTerminalError(RuntimeError):
    """A contracted A2A exchange ended without an accepted reply.

    Terminal by design: compiled retry predicates exclude it, even for
    ``retry_on: all``, because a native retry would redispatch the original
    request instead of continuing a bounded correction. ``code`` is a fixed
    identifier and the message never contains peer output.
    """

    def __init__(self, message: str, *, code: str, record: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.a2a_contract = record


class A2AReplyNotCompletedError(ValueError):
    """The original dispatch did not produce a completed reply.

    An ordinary execution failure (like an unsuccessful send), not a contract
    violation: it never starts a correction and keeps the node's own retry policy.
    """

    def __init__(self, message: str, *, record: dict[str, Any] | None = None):
        super().__init__(message)
        self.a2a_contract = record


def is_terminal_contract_error(exc: BaseException) -> bool:
    return isinstance(exc, A2AContractTerminalError)


@dataclass(frozen=True)
class ContractCheck:
    value: dict[str, Any] | None
    diagnostics: list[dict[str, Any]]

    @property
    def valid(self) -> bool:
        return self.value is not None and not self.diagnostics


@dataclass(frozen=True)
class A2AReply:
    """A classified A2A send result, before any contract validation."""

    kind: str  # "task" | "message" | "unknown"
    state: str  # normalized task state; "completed" for a direct agent message
    text: str
    task_id: str | None
    context_id: str | None
    message_id: str | None

    @property
    def completed(self) -> bool:
        return self.state == "completed"


_TASK_STATES = frozenset(
    {
        "submitted",
        "working",
        "input-required",
        "auth-required",
        "completed",
        "failed",
        "canceled",
        "cancelled",
        "rejected",
        "unknown",
    }
)


def _parts_text(parts: Any) -> str:
    if not isinstance(parts, list):
        return ""
    chunks: list[str] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        nested = part.get("root")
        root = nested if isinstance(nested, dict) else part
        text = root.get("text")
        if isinstance(text, str):
            chunks.append(text)
    return "".join(chunks)


def _optional_id(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def classify_reply(payload: Any) -> A2AReply:
    """Classify a successful ``A2AClient.send_message`` result payload.

    Text comes only from peer-authored content (an agent message, a task's
    status message, or its artifacts) -- never from task history, which also
    contains the caller's own request.
    """
    if not isinstance(payload, dict):
        return A2AReply("unknown", "unknown", "", None, None, None)
    kind = str(payload.get("kind") or "").strip().lower()
    if kind == "message" or (not kind and "parts" in payload and "status" not in payload):
        role = str(payload.get("role") or "").strip().lower()
        return A2AReply(
            "message",
            "completed" if role == "agent" else "unknown",
            _parts_text(payload.get("parts")) if role == "agent" else "",
            _optional_id(payload.get("taskId")),
            _optional_id(payload.get("contextId")),
            _optional_id(payload.get("messageId")),
        )
    status = payload.get("status")
    raw_state = str(status.get("state") or "").strip().lower() if isinstance(status, dict) else ""
    state = raw_state if raw_state in _TASK_STATES else "unknown"
    text = ""
    message_id = None
    if isinstance(status, dict) and isinstance(status.get("message"), dict):
        message = status["message"]
        if str(message.get("role") or "agent").strip().lower() != "user":
            text = _parts_text(message.get("parts"))
            message_id = _optional_id(message.get("messageId"))
    if not text.strip():
        artifacts = payload.get("artifacts")
        if isinstance(artifacts, list):
            for artifact in reversed(artifacts):
                if isinstance(artifact, dict):
                    text = _parts_text(artifact.get("parts"))
                    if text.strip():
                        break
    return A2AReply(
        "task",
        state,
        text,
        _optional_id(payload.get("id")),
        _optional_id(payload.get("contextId")),
        message_id,
    )


def _safe(token: Any) -> str | None:
    return token if isinstance(token, str) and _SAFE_TOKEN.match(token) else None


def _collect_property_names(schema: Any, names: set[str]) -> None:
    if isinstance(schema, dict):
        for keyword in ("properties", "patternProperties", "dependentRequired"):
            mapping = schema.get(keyword)
            if isinstance(mapping, dict):
                names.update(key for key in mapping if _safe(key))
        required = schema.get("required")
        if isinstance(required, list):
            names.update(item for item in required if _safe(item))
        for value in schema.values():
            _collect_property_names(value, names)
    elif isinstance(schema, list):
        for item in schema:
            _collect_property_names(item, names)


def _check_refs(schema: Any, defs: dict[str, Any]) -> None:
    if isinstance(schema, dict):
        for keyword in _REF_KEYWORDS:
            ref = schema.get(keyword)
            if ref is None:
                continue
            if not isinstance(ref, str) or not ref.startswith("#"):
                raise A2AContractConfigError(
                    "response_schema may only reference graph-local '#/$defs/<name>' schemas"
                )
            if ref.startswith(_DEFS_PREFIX) and ref[len(_DEFS_PREFIX) :] not in defs:
                raise A2AContractConfigError(
                    "response_schema references an undefined graph-local $defs entry"
                )
        for value in schema.values():
            _check_refs(value, defs)
    elif isinstance(schema, list):
        for item in schema:
            _check_refs(item, defs)


class ResponseContract:
    """A checked, offline JSON Schema contract for one A2A node."""

    def __init__(self, schema: dict[str, Any], defs: dict[str, dict[str, Any]] | None = None):
        try:
            from jsonschema import validators
            from referencing import Registry
        except ImportError as exc:  # pragma: no cover - a declared dependency
            raise A2AContractConfigError(
                "response_schema validation is unavailable (jsonschema is not installed)"
            ) from exc
        if not isinstance(schema, dict):
            raise A2AContractConfigError("response_schema must be a JSON object")
        merged_defs = {**(defs or {}), **(schema.get("$defs") or {})}
        _check_refs(schema, merged_defs)
        _check_refs(merged_defs, merged_defs)
        root = {**schema, "$defs": merged_defs} if merged_defs else dict(schema)
        validator_cls = validators.validator_for(root, default=validators.Draft202012Validator)
        try:
            validator_cls.check_schema(root)
        except Exception as exc:
            # The validator's message may quote schema text; keep it out.
            raise A2AContractConfigError("response_schema is not a valid JSON Schema") from exc
        # An empty registry never retrieves: no network or filesystem reference
        # resolution, only the graph-local $defs merged above.
        self._validator = validator_cls(root, registry=Registry())
        self._schema = root
        names: set[str] = set()
        _collect_property_names(root, names)
        self._known_names = names

    @property
    def schema(self) -> dict[str, Any]:
        return self._schema

    def check(self, text: str) -> ContractCheck:
        value, diagnostic = parse_sole_json_object(text)
        if diagnostic is not None:
            return ContractCheck(None, [diagnostic])
        assert value is not None
        diagnostics: list[dict[str, Any]] = []
        errors = sorted(
            self._validator.iter_errors(value),
            key=lambda error: (len(error.absolute_path), [str(p) for p in error.absolute_path]),
        )
        for error in errors:
            if len(diagnostics) >= _MAX_DIAGNOSTICS:
                diagnostics.append({"code": "more_problems_omitted"})
                break
            diagnostics.append(self._diagnostic(error))
        return ContractCheck(value, diagnostics)

    def required_summary(self) -> dict[str, Any]:
        """The minimum top-level constraints a correction needs to restate."""
        summary: dict[str, Any] = {"type": "object"}
        required = [name for name in self._schema.get("required") or [] if _safe(name)]
        if required:
            summary["required"] = required[:_MAX_LISTED_NAMES]
        if self._schema.get("additionalProperties") is False:
            allowed = [name for name in self._schema.get("properties") or {} if _safe(name)]
            summary["allowed_properties"] = allowed[:_MAX_LISTED_NAMES]
        return summary

    def _path(self, segments: Iterable[Any]) -> str:
        rendered = "$"
        for index, segment in enumerate(segments):
            if index >= _MAX_PATH_SEGMENTS:
                return rendered + ".…"
            if isinstance(segment, int):
                rendered += f"[{segment}]"
            elif isinstance(segment, str) and segment in self._known_names:
                rendered += f".{segment}"
            else:
                # A reply-side key the schema does not declare is peer text.
                rendered += ".<undeclared>"
        return rendered

    def _diagnostic(self, error: Any) -> dict[str, Any]:
        keyword = str(error.validator)
        expected: Any = error.validator_value
        diagnostic: dict[str, Any] = {"path": self._path(error.absolute_path)}
        if keyword == "required":
            present = set(error.instance) if isinstance(error.instance, dict) else set()
            missing = [name for name in expected or [] if name not in present and _safe(name)][
                :_MAX_LISTED_NAMES
            ]
            diagnostic.update(code="missing_required", missing=missing)
        elif keyword == "type":
            types = expected if isinstance(expected, list) else [expected]
            diagnostic.update(code="wrong_type", expected_type=[t for t in types if _safe(t)])
        elif keyword == "enum":
            diagnostic["code"] = "not_in_enum"
            allowed = _safe_scalars(expected)
            if allowed is not None:
                diagnostic["allowed"] = allowed
        elif keyword == "const":
            diagnostic["code"] = "const_mismatch"
            allowed = _safe_scalars([expected])
            if allowed is not None:
                diagnostic["expected"] = allowed[0]
        elif keyword in _RANGE_KEYWORDS:
            diagnostic["code"] = "out_of_range"
            if isinstance(expected, (int, float)) and not isinstance(expected, bool):
                diagnostic["constraint"] = {keyword: expected}
        elif keyword in {"additionalProperties", "unevaluatedProperties"}:
            schema = error.schema if isinstance(error.schema, dict) else {}
            allowed = [name for name in schema.get("properties") or {} if _safe(name)]
            diagnostic.update(
                code="unexpected_property", allowed_properties=allowed[:_MAX_LISTED_NAMES]
            )
        elif keyword in {"additionalItems", "unevaluatedItems"}:
            diagnostic["code"] = "unexpected_item"
        elif keyword == "pattern":
            diagnostic["code"] = "pattern_mismatch"
        elif keyword == "format":
            diagnostic["code"] = "format_mismatch"
            if _safe(expected):
                diagnostic["format"] = expected
        elif keyword in {"anyOf", "oneOf"}:
            diagnostic["code"] = "no_matching_alternative"
        elif keyword == "not":
            diagnostic["code"] = "forbidden_shape"
        elif keyword in {"dependentRequired", "dependencies"}:
            diagnostic["code"] = "missing_dependent_field"
        elif keyword == "uniqueItems":
            diagnostic["code"] = "duplicate_items"
        else:
            diagnostic["code"] = "schema_violation"
            if keyword in _KNOWN_KEYWORDS or _safe(keyword):
                diagnostic["keyword"] = keyword
        return diagnostic


def _safe_scalars(values: Any) -> list[Any] | None:
    if not isinstance(values, list) or len(values) > _MAX_LISTED_NAMES:
        return None
    for value in values:
        scalar = (
            value is None
            or isinstance(value, bool)
            or (isinstance(value, (int, float)) and math.isfinite(value))
            or (isinstance(value, str) and _safe(value))
        )
        if not scalar:
            return None
    return list(values)


class _DuplicateKey(ValueError):
    pass


class _NonFinite(ValueError):
    pass


def _pairs_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey()
        result[key] = value
    return result


def _reject_constant(_: str) -> Any:
    raise _NonFinite()


def _finite_float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise _NonFinite()
    return value


_DECODER = json.JSONDecoder(
    object_pairs_hook=_pairs_without_duplicates,
    parse_constant=_reject_constant,
    parse_float=_finite_float,
)


def parse_sole_json_object(text: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Parse ``text`` as exactly one JSON object, JSON whitespace allowed around it.

    Returns ``(value, None)`` or ``(None, diagnostic)``; the diagnostic never
    contains reply text.
    """
    if not isinstance(text, str) or not text.strip(_JSON_WHITESPACE):
        return None, {"path": "$", "code": "empty_reply"}
    start = len(text) - len(text.lstrip(_JSON_WHITESPACE))
    first = text[start]
    if first == "[":
        return None, {"path": "$", "code": "not_an_object", "expected_type": ["object"]}
    if first != "{":
        return None, {"path": "$", "code": "leading_content"}
    try:
        value, end = _DECODER.raw_decode(text, start)
    except _DuplicateKey:
        return None, {"path": "$", "code": "duplicate_key"}
    except _NonFinite:
        return None, {"path": "$", "code": "non_finite_number"}
    except json.JSONDecodeError as exc:
        return None, {
            "path": "$",
            "code": "malformed_json",
            "line": exc.lineno,
            "column": exc.colno,
        }
    except RecursionError:
        return None, {"path": "$", "code": "malformed_json"}
    if text[end:].strip(_JSON_WHITESPACE):
        return None, {"path": "$", "code": "trailing_content"}
    if not isinstance(value, dict):  # pragma: no cover - guarded by the '{' check
        return None, {"path": "$", "code": "not_an_object", "expected_type": ["object"]}
    return value, None


def is_declined_correction(text: str) -> bool:
    return isinstance(text, str) and text.strip() == _DECLINE_SENTINEL


def build_correction_request(contract: ResponseContract, diagnostics: list[dict[str, Any]]) -> str:
    """Fixed trusted instructions plus sanitized diagnostics.

    Deliberately excludes the original action prompt, routine state and the
    previous reply itself: the peer's own protocol context carries that reply.
    """
    problems = json.dumps(diagnostics[: _MAX_DIAGNOSTICS + 1], sort_keys=True)
    constraints = json.dumps(contract.required_summary(), sort_keys=True)
    return f"{CORRECTION_INSTRUCTIONS}{problems}\nTop-level contract constraints:\n{constraints}"


def browser_run_meta(run_meta: Any) -> Any:
    """``run_meta`` for browser delivery without raw A2A contract replies.

    The checkpoint keeps every original/corrected reply for the run's audit;
    UI events carry only per-attempt provenance, validity, diagnostic codes and
    reply length. The accepted reply is still the node's normal output.
    """
    if not isinstance(run_meta, dict):
        return run_meta
    projected: dict[str, Any] = {}
    for node_id, entries in run_meta.items():
        if not isinstance(entries, list):
            projected[node_id] = entries
            continue
        projected[node_id] = [_browser_entry(entry) for entry in entries]
    return projected


def _browser_entry(entry: Any) -> Any:
    record = entry.get("a2a_contract") if isinstance(entry, dict) else None
    if not isinstance(record, dict):
        return entry
    attempts = []
    for attempt in record.get("attempts") or []:
        if not isinstance(attempt, dict):
            continue
        visible = {key: value for key, value in attempt.items() if key != "reply"}
        if isinstance(attempt.get("reply"), str):
            visible["reply_chars"] = len(attempt["reply"])
        attempts.append(visible)
    return {**entry, "a2a_contract": {**record, "attempts": attempts}}
