"""Restricted expression evaluator for routine 'code'/'eval' nodes.

Evaluates a small, explicitly whitelisted subset of Python expression syntax via
``ast.parse(mode="eval")`` plus a recursive walk of the parsed tree — never
``eval()``/``exec()`` on the source. Anything outside the whitelist (calls, attribute
access, subscripting, imports, comprehensions, lambdas, ...) raises
``UnsafeExpressionError`` rather than silently failing closed/open, so a graph author
gets a clear compile-time-ish signal instead of a confusing downstream error.
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
        values = [_eval_node(value, namespace) for value in node.values]
        if isinstance(node.op, ast.And):
            result = True
            for value in values:
                result = value
                if not value:
                    break
            return result
        if isinstance(node.op, ast.Or):
            result = False
            for value in values:
                result = value
                if value:
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
