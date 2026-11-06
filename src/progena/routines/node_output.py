"""Strict shared node-output validation and scoped native retry feedback.

Schemas are checked before a graph runs. Raw replies must be one JSON object;
provider-parsed values receive the same schema checks. Diagnostics contain only
bounded schema-side information. A retry predicate publishes feedback through a
ContextVar, inherited by the matching native task without cross-run mutable state.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
import weakref
from collections.abc import Iterable
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from langgraph.runtime import Runtime

_MAX_DIAGNOSTICS = 20
_MAX_PATH_SEGMENTS = 8
_MAX_LISTED_NAMES = 32
_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.\-]{0,63}$")
_JSON_WHITESPACE = " \t\n\r"
_REF_KEYWORDS = ("$ref", "$dynamicRef", "$recursiveRef")
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

_OUTPUT_INSTRUCTIONS = (
    "Your previous response did not satisfy the required output schema. "
    "This is schema feedback only.\n"
    "Rules:\n"
    "- Preserve factual decisions already made; do not perform new work, call tools, "
    "send messages, modify orders, or conduct research.\n"
    "- Do not invent missing facts. If required facts are unavailable, say so honestly.\n"
    "- Reply with exactly one JSON object and nothing else: no Markdown, prose, or comments.\n"
    "Schema validation problems (codes, paths, and safe expected constraints only):\n"
)


class NodeOutputSchemaError(ValueError):
    """A declared node response schema cannot be enforced."""


@dataclass(frozen=True)
class OutputCheck:
    value: dict[str, Any] | None
    diagnostics: list[dict[str, Any]]

    @property
    def valid(self) -> bool:
        return self.value is not None and not self.diagnostics


@dataclass(frozen=True)
class OutputFeedback:
    node_id: str
    task_id: str
    reply: str
    diagnostics: list[dict[str, Any]]
    record: dict[str, Any]
    context_id: str | None = None


class NodeOutputValidationError(ValueError):
    """Retryable model-response validation failure with scoped feedback."""

    def __init__(self, node_id: str, *, feedback: OutputFeedback):
        super().__init__(f"node {node_id} output did not satisfy its response schema")
        self.node_id = node_id
        self.feedback = feedback
        self.node_output = feedback.record


class NodeOutputRetryError(RuntimeError):
    """Safe terminal output failure which must never be retried."""

    def __init__(self, message: str, *, code: str, record: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.node_output = record


_feedback: ContextVar[
    tuple[OutputFeedback, weakref.ReferenceType[asyncio.Task[Any]], object] | None
] = ContextVar("node_output_feedback", default=None)


def output_task_id(runtime: Runtime | None) -> str:
    """Return LangGraph's native task identity, or ``""`` outside graph execution."""
    execution = getattr(runtime, "execution_info", None) if runtime is not None else None
    task_id = getattr(execution, "task_id", None)
    return str(task_id) if task_id is not None else ""


def get_output_feedback(node_id: str, runtime: Runtime | None) -> OutputFeedback | None:
    """Get feedback only for this exact native node task.

    An absent task identity never matches: direct builder calls and nested/loop
    invocations cannot reuse another task's feedback.
    """
    task_id = output_task_id(runtime)
    scope = _feedback.get()
    if not task_id or scope is None:
        return None
    feedback, _task, _owner = scope
    return feedback if feedback.node_id == node_id and feedback.task_id == task_id else None


def publish_output_feedback(exc: BaseException, *, owner: object) -> bool:
    """Publish matching validation feedback when a native retry will occur."""
    if not isinstance(exc, NodeOutputValidationError):
        return False
    feedback = exc.feedback
    if not feedback.task_id or feedback.node_id != exc.node_id:
        return False
    try:
        task = asyncio.current_task()
    except RuntimeError:
        return False
    if task is None:
        return False
    _feedback.set((feedback, weakref.ref(task), owner))
    return True


def _output_feedback_for_node(node_id: str, *, owner: object) -> OutputFeedback | None:
    """Return feedback owned by this exact native retry callback and policy."""
    scope = _feedback.get()
    if scope is None:
        return None
    feedback, task_owner, policy_owner = scope
    try:
        current = asyncio.current_task()
    except RuntimeError:
        return None
    if (
        current is None
        or task_owner() is not current
        or policy_owner is not owner
        or not feedback.task_id
        or feedback.node_id != node_id
    ):
        return None
    return feedback


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
        for value in schema:
            _collect_property_names(value, names)


def _preflight_references(
    schema: Any, resolver: Any, specification: Any, visited: set[tuple[int, str]] | None = None
) -> None:
    if not isinstance(schema, dict):
        return
    visited = visited if visited is not None else set()
    key = (id(schema), str(getattr(resolver, "_base_uri", "")))
    if key in visited:
        return
    visited.add(key)
    for keyword in _REF_KEYWORDS:
        ref = schema.get(keyword)
        if ref is None:
            continue
        if not isinstance(ref, str) or not ref.startswith("#"):
            raise NodeOutputSchemaError(
                "response_schema $ref may only reference graph-local '#...' schemas"
            )
        resolver.lookup(ref)
    subresources = list(specification.subresources_of(schema))
    defs = schema.get("$defs")
    if isinstance(defs, dict):
        subresources.extend(defs.values())
    for subresource in subresources:
        _preflight_references(
            subresource,
            resolver.in_subresource(specification.create_resource(subresource)),
            specification,
            visited,
        )


class OutputSchema:
    """Offline-checked JSON Schema validation for any model-producing node."""

    def __init__(self, schema: dict[str, Any], defs: dict[str, Any] | None = None):
        try:
            from jsonschema import validators
            from referencing import Registry
            from referencing.jsonschema import specification_with
        except ImportError as exc:  # pragma: no cover - declared dependency
            raise NodeOutputSchemaError("response_schema validation is unavailable") from exc
        if not isinstance(schema, dict):
            raise NodeOutputSchemaError("response_schema must be a JSON object")
        if defs is not None and not isinstance(defs, dict):
            raise NodeOutputSchemaError("graph-local $defs must be a JSON object")
        inline_defs = schema.get("$defs")
        if inline_defs is not None and not isinstance(inline_defs, dict):
            raise NodeOutputSchemaError("response_schema $defs must be a JSON object")
        try:
            merged_defs = {**(defs or {}), **(inline_defs or {})}
            root = {**schema, "$defs": merged_defs} if merged_defs else dict(schema)
            dialect = root.get("$schema")
            supported = {
                identifier.removesuffix("#")
                for validator in (
                    validators.Draft3Validator,
                    validators.Draft4Validator,
                    validators.Draft6Validator,
                    validators.Draft7Validator,
                    validators.Draft201909Validator,
                    validators.Draft202012Validator,
                )
                if (identifier := validator.ID_OF(validator.META_SCHEMA)) is not None
            }
            if dialect is not None and (
                not isinstance(dialect, str) or dialect.removesuffix("#") not in supported
            ):
                raise NodeOutputSchemaError(
                    "response_schema declares an unsupported JSON Schema dialect"
                )
            validator_cls = validators.validator_for(root, default=validators.Draft202012Validator)
            validator_cls.check_schema(root)
            dialect_id = dialect or validators.Draft202012Validator.ID_OF(
                validators.Draft202012Validator.META_SCHEMA
            )
            assert dialect_id is not None
            specification = specification_with(dialect_id)
            registry = Registry()
            root_resolver = registry.resolver_with_root(specification.create_resource(root))
            _preflight_references(root, root_resolver, specification)
            self._validator = validator_cls(root, registry=registry)
            self._schema = root
            self._root_resolver = root_resolver
            names: set[str] = set()
            _collect_property_names(root, names)
            self._known_names = names
        except NodeOutputSchemaError:
            raise
        except Exception as exc:
            raise NodeOutputSchemaError("response_schema is not a valid JSON Schema") from exc

    def provider_schema(self) -> dict[str, Any]:
        """Resolve only root local-ref aliases for structured-output providers.

        The referenced object is structural guidance for generation, not a
        flattened replacement for local validation. Validation siblings remain
        conjunctive in the original schema; dictionary merging would overwrite
        them and can invent contradictions. Nested schemas/data stay untouched.
        """
        schema = self._schema
        resolver = self._root_resolver
        seen: set[str] = set()
        while isinstance(schema.get("$ref"), str) and schema["$ref"].startswith("#"):
            ref = schema["$ref"]
            if ref in seen:
                break
            seen.add(ref)
            resolved = resolver.lookup(ref)
            target = resolved.contents
            if not isinstance(target, dict):
                break
            schema = target
            resolver = resolved.resolver
        if schema is self._schema:
            return schema
        provider = dict(schema)
        if "$defs" in self._schema:
            provider.setdefault("$defs", self._schema["$defs"])
        for annotation in ("title", "description"):
            if annotation in self._schema:
                provider[annotation] = self._schema[annotation]
        return provider

    @property
    def schema(self) -> dict[str, Any]:
        return self._schema

    def check(self, text: str) -> OutputCheck:
        value, diagnostic = parse_sole_json_object(text)
        return (
            OutputCheck(None, [diagnostic]) if diagnostic is not None else self.check_value(value)
        )

    def check_value(self, value: Any) -> OutputCheck:
        if not isinstance(value, dict):
            return OutputCheck(
                None, [{"path": "$", "code": "not_an_object", "expected_type": ["object"]}]
            )
        diagnostic = _structured_value_diagnostic(value)
        if diagnostic is not None:
            return OutputCheck(None, [diagnostic])
        if _contains_invalid_surrogate(value):
            return OutputCheck(None, [{"path": "$", "code": "invalid_unicode"}])
        try:
            errors = sorted(
                self._validator.iter_errors(value),
                key=lambda error: (
                    len(error.absolute_path),
                    [str(part) for part in error.absolute_path],
                ),
            )
            diagnostics = [self._diagnostic(error) for error in errors[:_MAX_DIAGNOSTICS]]
            if len(errors) > _MAX_DIAGNOSTICS:
                diagnostics.append({"code": "more_problems_omitted"})
            return OutputCheck(value, diagnostics)
        except Exception:
            return OutputCheck(value, [{"path": "$", "code": "schema_validation_failed"}])

    def required_summary(self) -> dict[str, Any]:
        summary: dict[str, Any] = {"type": "object"}
        required = [name for name in self._schema.get("required") or [] if _safe(name)]
        if required:
            summary["required"] = required[:_MAX_LISTED_NAMES]
        if self._schema.get("additionalProperties") is False:
            summary["allowed_properties"] = [
                name for name in self._schema.get("properties") or {} if _safe(name)
            ][:_MAX_LISTED_NAMES]
        return summary

    def _path(self, segments: Iterable[Any]) -> str:
        result = "$"
        for index, segment in enumerate(segments):
            if index >= _MAX_PATH_SEGMENTS:
                return result + ".…"
            result += (
                f"[{segment}]"
                if isinstance(segment, int)
                else f".{segment}"
                if isinstance(segment, str) and segment in self._known_names
                else ".<undeclared>"
            )
        return result

    def _diagnostic(self, error: Any) -> dict[str, Any]:
        keyword, expected = str(error.validator), error.validator_value
        result: dict[str, Any] = {"path": self._path(error.absolute_path)}
        if keyword == "required":
            present = set(error.instance) if isinstance(error.instance, dict) else set()
            result.update(
                code="missing_required",
                missing=[name for name in expected or [] if name not in present and _safe(name)][
                    :_MAX_LISTED_NAMES
                ],
            )
        elif keyword == "type":
            values = expected if isinstance(expected, list) else [expected]
            result.update(
                code="wrong_type", expected_type=[value for value in values if _safe(value)]
            )
        elif keyword in {"enum", "const"}:
            allowed = _safe_scalars(expected if keyword == "enum" else [expected])
            result["code"] = "not_in_enum" if keyword == "enum" else "const_mismatch"
            if allowed is not None:
                result["allowed" if keyword == "enum" else "expected"] = (
                    allowed if keyword == "enum" else allowed[0]
                )
        elif keyword in _RANGE_KEYWORDS:
            result["code"] = "out_of_range"
            if isinstance(expected, (int, float)) and not isinstance(expected, bool):
                result["constraint"] = {keyword: expected}
        elif keyword in {"additionalProperties", "unevaluatedProperties"}:
            result.update(
                code="unexpected_property",
                allowed_properties=[
                    name
                    for name in (error.schema if isinstance(error.schema, dict) else {}).get(
                        "properties"
                    )
                    or {}
                    if _safe(name)
                ][:_MAX_LISTED_NAMES],
            )
        elif keyword in {"additionalItems", "unevaluatedItems"}:
            result["code"] = "unexpected_item"
        elif keyword in {"pattern", "format"}:
            result["code"] = f"{keyword}_mismatch"
            if keyword == "format" and _safe(expected):
                result["format"] = expected
        elif keyword in {"anyOf", "oneOf"}:
            result["code"] = "no_matching_alternative"
        elif keyword == "not":
            result["code"] = "forbidden_shape"
        elif keyword in {"dependentRequired", "dependencies"}:
            result["code"] = "missing_dependent_field"
        elif keyword == "uniqueItems":
            result["code"] = "duplicate_items"
        else:
            result["code"] = "schema_violation"
            if keyword in _KNOWN_KEYWORDS or _safe(keyword):
                result["keyword"] = keyword
        return result


def build_output_feedback(schema: OutputSchema, diagnostics: list[dict[str, Any]]) -> str:
    """Build trusted schema feedback without quoting an untrusted reply."""
    problems = json.dumps(diagnostics[: _MAX_DIAGNOSTICS + 1], sort_keys=True)
    constraints = json.dumps(schema.required_summary(), sort_keys=True)
    return f"{_OUTPUT_INSTRUCTIONS}{problems}\nTop-level schema constraints:\n{constraints}"


def _safe_scalars(values: Any) -> list[Any] | None:
    if not isinstance(values, list) or len(values) > _MAX_LISTED_NAMES:
        return None
    if any(
        not (
            value is None
            or isinstance(value, bool)
            or (isinstance(value, (int, float)) and math.isfinite(value))
            or (isinstance(value, str) and _safe(value))
        )
        for value in values
    ):
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


def _contains_invalid_surrogate(value: Any) -> bool:
    values = [value]
    while values:
        current = values.pop()
        if isinstance(current, dict):
            values.extend(current.keys())
            values.extend(current.values())
        elif isinstance(current, list):
            values.extend(current)
        elif isinstance(current, str):
            index = 0
            while index < len(current):
                codepoint = ord(current[index])
                if 0xD800 <= codepoint <= 0xDBFF:
                    if index + 1 >= len(current) or not 0xDC00 <= ord(current[index + 1]) <= 0xDFFF:
                        return True
                    index += 2
                elif 0xDC00 <= codepoint <= 0xDFFF:
                    return True
                else:
                    index += 1
    return False


def _structured_value_diagnostic(value: Any) -> dict[str, Any] | None:
    """Reject provider values that cannot be represented safely in checkpoints."""
    try:
        json.dumps(value, allow_nan=False)
    except ValueError as exc:
        if "Out of range float" in str(exc):
            return {"path": "$", "code": "non_finite_number"}
        return {"path": "$", "code": "malformed_json"}
    except (OverflowError, RecursionError, TypeError):
        return {"path": "$", "code": "malformed_json"}

    values = [value]
    while values:
        current = values.pop()
        if isinstance(current, dict):
            if any(not isinstance(key, str) for key in current):
                return {"path": "$", "code": "malformed_json"}
            values.extend(current.values())
        elif isinstance(current, list):
            values.extend(current)
        elif isinstance(current, tuple):
            return {"path": "$", "code": "malformed_json"}
    return None


_DECODER = json.JSONDecoder(
    object_pairs_hook=_pairs_without_duplicates,
    parse_constant=_reject_constant,
    parse_float=_finite_float,
)


def parse_sole_json_object(text: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Parse exactly one finite JSON object with no duplicate keys or extra text."""
    if not isinstance(text, str) or not text.strip(_JSON_WHITESPACE):
        return None, {"path": "$", "code": "empty_reply"}
    start = len(text) - len(text.lstrip(_JSON_WHITESPACE))
    if text[start] == "[":
        return None, {"path": "$", "code": "not_an_object", "expected_type": ["object"]}
    if text[start] != "{":
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
    except (RecursionError, ValueError):
        return None, {"path": "$", "code": "malformed_json"}
    if text[end:].strip(_JSON_WHITESPACE):
        return None, {"path": "$", "code": "trailing_content"}
    if not isinstance(value, dict):  # pragma: no cover - opening brace guards this
        return None, {"path": "$", "code": "not_an_object", "expected_type": ["object"]}
    if _contains_invalid_surrogate(value):
        return None, {"path": "$", "code": "invalid_unicode"}
    return value, None


def browser_run_meta(run_meta: Any) -> Any:
    """Project node-output audit records for browser delivery without raw replies."""
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
    record = entry.get("node_output") if isinstance(entry, dict) else None
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
    return {**entry, "node_output": {**record, "attempts": attempts}}
