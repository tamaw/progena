"""Tests for the restricted expression evaluator used by routine code/eval nodes."""

from __future__ import annotations

import pytest

from app.progenitor.routines.expression import UnsafeExpressionError, evaluate_expression


class TestAllowedConstructs:
    def test_constants(self):
        assert evaluate_expression("None", {}) is None
        assert evaluate_expression("42", {}) == 42
        assert evaluate_expression("3.5", {}) == 3.5
        assert evaluate_expression("'hi'", {}) == "hi"
        assert evaluate_expression("True", {}) is True

    def test_name_lookup(self):
        assert evaluate_expression("host", {"host": "github.com"}) == "github.com"

    def test_name_lookup_missing_raises_keyerror(self):
        with pytest.raises(KeyError):
            evaluate_expression("missing", {})

    def test_arithmetic(self):
        assert evaluate_expression("1 + 2 * 3", {}) == 7
        assert evaluate_expression("7 // 2", {}) == 3
        assert evaluate_expression("7 % 2", {}) == 1
        assert evaluate_expression("2 ** 3", {}) == 8

    def test_unary(self):
        assert evaluate_expression("-x", {"x": 5}) == -5
        assert evaluate_expression("+x", {"x": 5}) == 5
        assert evaluate_expression("not x", {"x": False}) is True

    def test_boolop_and_or(self):
        assert evaluate_expression("a and b", {"a": True, "b": False}) is False
        assert evaluate_expression("a or b", {"a": False, "b": True}) is True

    def test_compare_chained(self):
        assert evaluate_expression("1 < x < 10", {"x": 5}) is True
        assert evaluate_expression("x in items", {"x": 2, "items": [1, 2, 3]}) is True
        assert evaluate_expression("x not in items", {"x": 9, "items": [1, 2, 3]}) is True

    def test_ifexp_ternary(self):
        assert evaluate_expression("'yes' if flag else 'no'", {"flag": True}) == "yes"
        assert evaluate_expression("'yes' if flag else 'no'", {"flag": False}) == "no"

    def test_repo_setup_worker_domain_check_expression(self):
        assert evaluate_expression("host == 'github.com'", {"host": "github.com"}) is True
        assert evaluate_expression("host == 'github.com'", {"host": "gitlab.com"}) is False

    def test_repo_setup_worker_route_decision_expression(self):
        expression = (
            "'no_access' if not accessible else ('not_cloned' if not cloned else "
            "(('dirty_override' if override_dirty else 'dirty') if dirty else "
            "('behind' if behind else 'up_to_date')))"
        )
        namespace = {
            "accessible": True,
            "cloned": True,
            "dirty": False,
            "behind": False,
            "override_dirty": False,
        }
        assert evaluate_expression(expression, namespace) == "up_to_date"
        namespace["dirty"] = True
        assert evaluate_expression(expression, namespace) == "dirty"
        namespace["override_dirty"] = True
        assert evaluate_expression(expression, namespace) == "dirty_override"


class TestRejectedConstructs:
    def test_rejects_call(self):
        with pytest.raises(UnsafeExpressionError):
            evaluate_expression("len(x)", {"x": [1, 2]})

    def test_rejects_attribute_access(self):
        with pytest.raises(UnsafeExpressionError):
            evaluate_expression("x.upper", {"x": "hi"})

    def test_rejects_subscript(self):
        with pytest.raises(UnsafeExpressionError):
            evaluate_expression("x[0]", {"x": [1, 2]})

    def test_rejects_import(self):
        with pytest.raises(UnsafeExpressionError):
            evaluate_expression("__import__('os')", {})

    def test_rejects_comprehension(self):
        with pytest.raises(UnsafeExpressionError):
            evaluate_expression("[i for i in x]", {"x": [1, 2]})

    def test_rejects_lambda(self):
        with pytest.raises(UnsafeExpressionError):
            evaluate_expression("(lambda: 1)()", {})

    def test_rejects_invalid_syntax(self):
        with pytest.raises(UnsafeExpressionError):
            evaluate_expression("x = 1", {})

    def test_rejects_fstring_and_other_constants(self):
        with pytest.raises(UnsafeExpressionError):
            evaluate_expression("...", {})
