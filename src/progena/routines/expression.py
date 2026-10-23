"""Restricted expression evaluator for routine 'eval'/'condition'/'switch' nodes.

Evaluates a small, explicitly whitelisted subset of Python expression syntax via
``ast.parse(mode="eval")`` plus a recursive walk of the parsed tree — never
``eval()``/``exec()`` on the source. Anything outside the whitelist (calls,
subscripting, imports, comprehensions, lambdas, ...) raises
``UnsafeExpressionError`` rather than silently failing closed/open, so a graph author
gets a clear compile-time-ish signal instead of a confusing downstream error.
Dotted attribute access (``a.b.c``) is allowed, but only ever as a dict-key walk --
never Python's real ``getattr`` -- so it can only reach into nested JSON-shaped state
objects, not methods, dunder attributes, or anything else on an arbitrary object.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from typing import Any

_MAX_EXPONENT = 1000
_MAX_INT_RESULT_BITS = 100_000
# Shared cap on any str/bytes/list/tuple built by `*` or `+` (simpleeval's MAX_STRING_LENGTH).
_MAX_SEQUENCE_LENGTH = 100_000
_SEQUENCE_TYPES = (str, bytes, list, tuple)
# Bounds parse/walk cost; remaining operators only grow linearly with length.
_MAX_EXPRESSION_LENGTH = 2000
# Per-evaluation cap on logical entries built *and* compared across the whole expression.
# The per-result cap alone still lets one expression repeat a capped construction
# (`[{**m}, {**m}, ...]`) or deep-compare shared references a few hundred times.
_MAX_TOTAL_ALLOCATION = 1_000_000
# Bounds the evaluator's own recursion; a 2000-character `1 + 1 + ...` chain is ~500 deep.
_MAX_AST_DEPTH = 500
# Bounds the size walk below and any built-in comparison it guards.
_MAX_NESTING_DEPTH = 64
_CONTAINER_TYPES = (list, tuple, dict, set, frozenset)


def _safe_pow(a: Any, b: Any) -> Any:
    if isinstance(b, int) and b > _MAX_EXPONENT:
        raise UnsafeExpressionError(
            f"exponent too large ({b} > {_MAX_EXPONENT}); power operations are bounded"
        )
    # A bounded exponent still explodes when chained ((10 ** 1000) ** 1000).
    if (
        isinstance(a, int)
        and isinstance(b, int)
        and b > 0
        and a.bit_length() * b > _MAX_INT_RESULT_BITS
    ):
        raise UnsafeExpressionError(
            f"power result too large (over {_MAX_INT_RESULT_BITS} bits); power operations are bounded"
        )
    return a**b


def _safe_mult(a: Any, b: Any) -> Any:
    # Check the repeated length before computing to avoid allocating huge sequences.
    for seq, times in ((a, b), (b, a)):
        if (
            isinstance(seq, _SEQUENCE_TYPES)
            and isinstance(times, int)
            and len(seq) * times > _MAX_SEQUENCE_LENGTH
        ):
            raise UnsafeExpressionError(
                f"sequence repetition result too long (over {_MAX_SEQUENCE_LENGTH} items)"
            )
    return a * b  # type: ignore[operator]


def _safe_add(a: Any, b: Any) -> Any:
    # Repeated concatenation is the other way to grow a sequence from small literals.
    if (
        isinstance(a, _SEQUENCE_TYPES)
        and isinstance(b, _SEQUENCE_TYPES)
        and len(a) + len(b) > _MAX_SEQUENCE_LENGTH
    ):
        raise UnsafeExpressionError(
            f"sequence concatenation result too long (over {_MAX_SEQUENCE_LENGTH} items)"
        )
    return a + b  # type: ignore[operator]


def _safe_mod(a: Any, b: Any) -> Any:
    if isinstance(a, str):
        raise UnsafeExpressionError("string formatting with '%' is not allowed")
    return a % b


_ALLOWED_BINOPS: dict[type[ast.operator], Any] = {
    ast.Add: _safe_add,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: _safe_mult,
    ast.Div: lambda a, b: a / b,
    ast.FloorDiv: lambda a, b: a // b,
    ast.Mod: _safe_mod,
    ast.Pow: _safe_pow,
}

_ALLOWED_COMPARES: dict[type[ast.cmpop], Any] = {
    ast.Eq: lambda a, b: a == b,
    ast.NotEq: lambda a, b: a != b,
    ast.Lt: lambda a, b: a < b,
    ast.LtE: lambda a, b: a <= b,
    ast.Gt: lambda a, b: a > b,
    ast.GtE: lambda a, b: a >= b,
    ast.In: lambda a, b: a in b,
    ast.NotIn: lambda a, b: a not in b,
}


class UnsafeExpressionError(ValueError):
    """Raised when an expression uses syntax outside the restricted whitelist."""


def evaluate_expression(expression: str, namespace: dict[str, Any]) -> Any:
    """Evaluate ``expression`` against ``namespace``, rejecting unsafe syntax."""
    if len(expression) > _MAX_EXPRESSION_LENGTH:
        raise UnsafeExpressionError(
            f"expression too long ({len(expression)} > {_MAX_EXPRESSION_LENGTH} characters)"
        )
    try:
        tree = ast.parse(expression, mode="eval")
    except (SyntaxError, RecursionError) as exc:
        raise UnsafeExpressionError(f"invalid expression syntax: {exc}") from exc
    _check_ast_depth(tree.body)
    return _eval_node(tree.body, namespace, _EvaluationBudget())


def _check_ast_depth(root: ast.AST) -> None:
    stack = [(root, 1)]
    while stack:
        node, depth = stack.pop()
        if depth > _MAX_AST_DEPTH:
            raise UnsafeExpressionError(f"expression nested too deeply (over {_MAX_AST_DEPTH})")
        stack.extend((child, depth + 1) for child in ast.iter_child_nodes(node))


_EXHAUSTED = object()


def _logical_size(value: Any, limit: int) -> int:
    """Entries ``value`` expands to when walked, counting a shared reference every time
    it appears -- so ``[[0] * 1000] * 1000`` is 1,000,000, not 1,001.

    Iterative, nesting- and cycle-checked, and returns ``limit + 1`` as soon as the
    count passes ``limit``, so measuring costs at most about ``limit`` steps.
    """
    total = 0
    path: list[int] = []  # ids of the containers currently being walked
    iterators: list[Iterator[Any]] = []
    item = value
    while True:
        if isinstance(item, _CONTAINER_TYPES) and item:
            if id(item) in path:
                raise UnsafeExpressionError("value contains a reference cycle")
            if len(path) >= _MAX_NESTING_DEPTH:
                raise UnsafeExpressionError(
                    f"value nested too deeply (over {_MAX_NESTING_DEPTH} levels)"
                )
            path.append(id(item))
            iterators.append(iter(item.values() if isinstance(item, dict) else item))
        else:
            # Scalars and empty containers count once; strings count their length.
            total += max(1, len(item)) if isinstance(item, (str, bytes)) else 1
            if total > limit:
                return limit + 1
        while iterators:
            item = next(iterators[-1], _EXHAUSTED)
            if item is not _EXHAUSTED:
                break
            iterators.pop()
            path.pop()
        else:
            return total


class _EvaluationBudget:
    """Logical entries one evaluation may still build or compare; shared by every step."""

    def __init__(self) -> None:
        self.remaining = _MAX_TOTAL_ALLOCATION

    def charge(self, entries: int) -> None:
        if entries > self.remaining:
            raise UnsafeExpressionError(
                f"expression builds too much data (over {_MAX_TOTAL_ALLOCATION} entries in total)"
            )
        self.remaining -= entries

    def size(self, value: Any, limit: int = _MAX_SEQUENCE_LENGTH) -> int:
        return _logical_size(value, limit)

    def charge_result(self, entries: int, message: str, work: int = 0) -> None:
        """Check a construction's logical size against the per-result cap *before* it is
        materialized, then charge it (or the measuring ``work``, if larger)."""
        if entries > _MAX_SEQUENCE_LENGTH:
            raise UnsafeExpressionError(message)
        self.charge(max(entries, work))

    def charge_comparison(self, op: ast.cmpop, left: Any, right: Any) -> None:
        """Charge a conservative bound on a built-in comparison before running it.

        ``==``/``<``/``in`` recurse through nested containers in C; measuring both
        operands first, each bounded by what is left, keeps that recursion bounded too.
        """
        left_size = self.size(left, self.remaining)
        self.charge(left_size)
        membership = isinstance(op, (ast.In, ast.NotIn))
        if membership and isinstance(right, (dict, set, frozenset)):
            self.charge(left_size)  # hash the needle, compare it with at most one key
            return
        self.charge(self.size(right, self.remaining))
        if membership and isinstance(right, (list, tuple)):
            # Each element may be compared with the whole needle.
            self.charge(max(len(right) - 1, 0) * left_size)


def _eval_node(node: ast.AST, namespace: dict[str, Any], budget: _EvaluationBudget) -> Any:
    if isinstance(node, ast.Constant):
        if node.value is None or isinstance(node.value, (bool, int, float, str)):
            return node.value
        raise UnsafeExpressionError(f"unsupported constant type: {type(node.value).__name__}")

    if isinstance(node, ast.Name):
        if node.id not in namespace:
            raise KeyError(f"expression references undefined name: {node.id}")
        return namespace[node.id]

    if isinstance(node, ast.Attribute):
        # `a.b.c` walks nested dicts exactly the way a config-authored dotted
        # path (`resolve_data_path`) already does -- this is a parallel,
        # AST-native version of the same walk, not a generic Python
        # `getattr`. Only ever indexes into a dict via `.get`-equivalent
        # lookup; a non-dict base has no attributes this evaluator will ever
        # resolve, which is what keeps this from becoming a route to method
        # calls, dunder access, or anything else on an arbitrary object.
        base = _eval_node(node.value, namespace, budget)
        if not isinstance(base, dict):
            raise UnsafeExpressionError(
                f"cannot access '.{node.attr}' on a {type(base).__name__} -- "
                "attribute access only walks nested state objects"
            )
        if node.attr not in base:
            raise KeyError(f"expression references undefined nested field: .{node.attr}")
        return base[node.attr]

    if isinstance(node, ast.BinOp):
        op_fn = _ALLOWED_BINOPS.get(type(node.op))
        if op_fn is None:
            raise UnsafeExpressionError(f"unsupported binary operator: {type(node.op).__name__}")
        left = _eval_node(node.left, namespace, budget)
        right = _eval_node(node.right, namespace, budget)
        _charge_sequence_op(node.op, left, right, budget)
        return op_fn(left, right)

    if isinstance(node, ast.UnaryOp):
        operand = _eval_node(node.operand, namespace, budget)
        if isinstance(node.op, ast.UAdd):
            return +operand
        if isinstance(node.op, ast.USub):
            return -operand
        if isinstance(node.op, ast.Not):
            return not operand
        raise UnsafeExpressionError(f"unsupported unary operator: {type(node.op).__name__}")

    if isinstance(node, ast.BoolOp):
        # Operands are evaluated lazily, one at a time, so `and`/`or` short-circuit
        # exactly like Python's. Evaluating them all up front would defeat the guard
        # idiom every author reaches for -- `x != 0 and 100 / x > 2` must not divide,
        # and `has_org and org == 'acme'` must not require `org` to be bound.
        if isinstance(node.op, ast.And):
            result: Any = True
            for value_node in node.values:
                result = _eval_node(value_node, namespace, budget)
                if not result:
                    break
            return result
        if isinstance(node.op, ast.Or):
            result = False
            for value_node in node.values:
                result = _eval_node(value_node, namespace, budget)
                if result:
                    break
            return result
        raise UnsafeExpressionError(f"unsupported boolean operator: {type(node.op).__name__}")

    if isinstance(node, ast.Compare):
        left = _eval_node(node.left, namespace, budget)
        for op, comparator in zip(node.ops, node.comparators, strict=True):
            op_fn = _ALLOWED_COMPARES.get(type(op))
            if op_fn is None:
                raise UnsafeExpressionError(f"unsupported comparison operator: {type(op).__name__}")
            right = _eval_node(comparator, namespace, budget)
            budget.charge_comparison(op, left, right)
            if not op_fn(left, right):
                return False
            left = right
        return True

    if isinstance(node, ast.IfExp):
        if _eval_node(node.test, namespace, budget):
            return _eval_node(node.body, namespace, budget)
        return _eval_node(node.orelse, namespace, budget)

    if isinstance(node, ast.Dict):
        return _eval_dict(node, namespace, budget)

    if isinstance(node, ast.List):
        # `[*x]` would be a sequence unpack; only mapping unpack inside `{}` is supported.
        too_long = f"list literal too long (over {_MAX_SEQUENCE_LENGTH} items)"
        if len(node.elts) > _MAX_SEQUENCE_LENGTH:
            raise UnsafeExpressionError(too_long)
        items: list[Any] = []
        for element in node.elts:
            if isinstance(element, ast.Starred):
                raise UnsafeExpressionError("list unpacking with '*' is not allowed")
            items.append(_eval_node(element, namespace, budget))
        # Counted logically, so `[m, m, ...]` pays for every copy of `m` a reader walks.
        logical = 0
        for item in items:
            logical += budget.size(item, _MAX_SEQUENCE_LENGTH - logical)
            if logical > _MAX_SEQUENCE_LENGTH:
                break
        budget.charge_result(logical, too_long)
        return items

    raise UnsafeExpressionError(f"unsupported expression syntax: {type(node).__name__}")


def _eval_dict(
    node: ast.Dict, namespace: dict[str, Any], budget: _EvaluationBudget
) -> dict[str, Any]:
    # JSON object literal. `**base` entries and explicit keys merge left to right,
    # later keys overwriting earlier ones, into a fresh dict -- an unpacked state
    # object is copied from, never mutated.
    too_large = f"object literal too large (over {_MAX_SEQUENCE_LENGTH} keys)"
    result: dict[str, Any] = {}
    logical = 0  # nested entries so far, counting each `**` source in full
    for key_node, value_node in zip(node.keys, node.values, strict=True):
        if key_node is None:
            source = _eval_node(value_node, namespace, budget)
            if not isinstance(source, dict):
                raise UnsafeExpressionError(
                    f"cannot unpack '**' from a {type(source).__name__} -- "
                    "only objects can be unpacked"
                )
            # Checked before copying so a huge state object is never duplicated.
            source_size = budget.size(source, _MAX_SEQUENCE_LENGTH - logical)
            logical += source_size
            if logical > _MAX_SEQUENCE_LENGTH:
                raise UnsafeExpressionError(too_large)
            budget.charge(source_size)
            for source_key in source:
                if not isinstance(source_key, str):
                    raise UnsafeExpressionError(
                        f"object keys must be strings, not {type(source_key).__name__}"
                    )
            result.update(source)
            continue
        key = _eval_node(key_node, namespace, budget)
        if not isinstance(key, str):
            raise UnsafeExpressionError(f"object keys must be strings, not {type(key).__name__}")
        value = _eval_node(value_node, namespace, budget)
        value_size = budget.size(value, _MAX_SEQUENCE_LENGTH - logical)
        logical += value_size
        if logical > _MAX_SEQUENCE_LENGTH:
            raise UnsafeExpressionError(too_large)
        budget.charge(value_size)
        result[key] = value
    return result


def _charge_sequence_op(op: ast.operator, left: Any, right: Any, budget: _EvaluationBudget) -> None:
    # Counts the logical result before the operator builds anything: `[[0] * 1000] * 1000`
    # is a million entries to any later walk even though Python allocates two short lists.
    if (
        isinstance(op, ast.Add)
        and isinstance(left, _SEQUENCE_TYPES)
        and isinstance(right, _SEQUENCE_TYPES)
    ):
        left_size = budget.size(left)
        right_size = budget.size(right)
        budget.charge_result(
            left_size + right_size,
            f"sequence concatenation result too long (over {_MAX_SEQUENCE_LENGTH} items)",
        )
    elif isinstance(op, ast.Mult):
        for seq, times in ((left, right), (right, left)):
            if isinstance(seq, _SEQUENCE_TYPES) and isinstance(times, int):
                seq_size = budget.size(seq)
                budget.charge_result(
                    seq_size * max(times, 0) if seq else 0,
                    f"sequence repetition result too long (over {_MAX_SEQUENCE_LENGTH} items)",
                    work=seq_size,
                )
                return
