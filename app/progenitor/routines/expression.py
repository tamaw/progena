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
from typing import Any

_ALLOWED_BINOPS: dict[type[ast.operator], Any] = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
    ast.FloorDiv: lambda a, b: a // b,
    ast.Mod: lambda a, b: a % b,
    ast.Pow: lambda a, b: a**b,
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
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise UnsafeExpressionError(f"invalid expression syntax: {exc}") from exc
    return _eval_node(tree.body, namespace)


def _eval_node(node: ast.AST, namespace: dict[str, Any]) -> Any:
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
        base = _eval_node(node.value, namespace)
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
        return op_fn(_eval_node(node.left, namespace), _eval_node(node.right, namespace))

    if isinstance(node, ast.UnaryOp):
        operand = _eval_node(node.operand, namespace)
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
                result = _eval_node(value_node, namespace)
                if not result:
                    break
            return result
        if isinstance(node.op, ast.Or):
            result = False
            for value_node in node.values:
                result = _eval_node(value_node, namespace)
                if result:
                    break
            return result
        raise UnsafeExpressionError(f"unsupported boolean operator: {type(node.op).__name__}")

    if isinstance(node, ast.Compare):
        left = _eval_node(node.left, namespace)
        for op, comparator in zip(node.ops, node.comparators, strict=True):
            op_fn = _ALLOWED_COMPARES.get(type(op))
            if op_fn is None:
                raise UnsafeExpressionError(f"unsupported comparison operator: {type(op).__name__}")
            right = _eval_node(comparator, namespace)
            if not op_fn(left, right):
                return False
            left = right
        return True

    if isinstance(node, ast.IfExp):
        if _eval_node(node.test, namespace):
            return _eval_node(node.body, namespace)
        return _eval_node(node.orelse, namespace)

    raise UnsafeExpressionError(f"unsupported expression syntax: {type(node).__name__}")
