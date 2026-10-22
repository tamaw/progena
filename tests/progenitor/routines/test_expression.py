"""Tests for the restricted expression evaluator used by routine code/eval nodes."""

from __future__ import annotations

import pytest

from src.progenitor.routines.expression import UnsafeExpressionError, evaluate_expression


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

    def test_nested_attribute_access(self):
        assert evaluate_expression("parse.host", {"parse": {"host": "github.com"}}) == "github.com"

    def test_chained_nested_attribute_access(self):
        namespace = {"assess": {"repo": {"owner": {"name": "octocat"}}}}
        assert evaluate_expression("assess.repo.owner.name", namespace) == "octocat"

    def test_nested_attribute_access_missing_key_raises_keyerror(self):
        with pytest.raises(KeyError):
            evaluate_expression("parse.host", {"parse": {}})

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

    def test_boolop_returns_the_deciding_operand_not_a_bool(self):
        # Python's `and`/`or` are value-selecting, not bool-coercing. `code` nodes
        # store this result raw, so the distinction is observable downstream.
        assert evaluate_expression("a and b", {"a": 0, "b": "x"}) == 0
        assert evaluate_expression("a or b", {"a": "", "b": "fallback"}) == "fallback"

    def test_boolop_and_short_circuits_past_a_failing_guard(self):
        # The guard is the only thing standing between the divisor and zero.
        expression = "x != 0 and 100 / x > 2"
        assert evaluate_expression(expression, {"x": 0}) is False
        # Same expression, guard satisfied: the right operand now really is evaluated.
        assert evaluate_expression(expression, {"x": 1}) is True

    def test_boolop_or_short_circuits_past_a_satisfied_guard(self):
        expression = "x == 0 or 100 / x > 2"
        assert evaluate_expression(expression, {"x": 0}) is True
        assert evaluate_expression(expression, {"x": 1}) is True

    def test_boolop_short_circuits_past_an_unbound_name(self):
        # Optional-field checks: `org` need not be bound when the guard is false.
        expression = "has_org and org == 'acme'"
        assert evaluate_expression(expression, {"has_org": False}) is False
        # But a satisfied guard must still surface the missing binding rather than
        # silently treating it as absent.
        with pytest.raises(KeyError):
            evaluate_expression(expression, {"has_org": True})

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


RECEIPT = '{**card.metadata, "review_approval": {"inbox_id": inbox.id, "sha": evidence.commit_sha}}'


class TestObjectAndListLiterals:
    def test_approval_receipt_preserves_unrelated_metadata(self):
        metadata = {"priority": "high", "labels": ["infra"]}
        namespace = {
            "card": {"metadata": metadata},
            "inbox": {"id": "msg-1"},
            "evidence": {"commit_sha": "abc123"},
        }
        result = evaluate_expression(RECEIPT, namespace)
        assert result == {
            "priority": "high",
            "labels": ["infra"],
            "review_approval": {"inbox_id": "msg-1", "sha": "abc123"},
        }
        # A new object: the unpacked state is never mutated.
        assert result is not metadata
        assert metadata == {"priority": "high", "labels": ["infra"]}

    def test_list_values(self):
        assert evaluate_expression("[]", {}) == []
        assert evaluate_expression("[1, x, 'a', None]", {"x": 2}) == [1, 2, "a", None]
        assert evaluate_expression('{"tags": [a, [1, 2]], "n": {}}', {"a": "x"}) == {
            "tags": ["x", [1, 2]],
            "n": {},
        }

    def test_later_keys_overwrite_earlier_left_to_right(self):
        namespace = {"a": {"k": 1, "x": "a"}, "b": {"k": 2}}
        assert evaluate_expression("{**a, **b}", namespace) == {"k": 2, "x": "a"}
        assert evaluate_expression('{**a, "k": 3}', namespace) == {"k": 3, "x": "a"}
        assert evaluate_expression('{"k": 3, **a}', namespace) == {"k": 1, "x": "a"}

    def test_rejects_non_string_keys(self):
        with pytest.raises(UnsafeExpressionError, match="keys must be strings"):
            evaluate_expression("{1: 'x'}", {})
        with pytest.raises(UnsafeExpressionError, match="keys must be strings"):
            evaluate_expression("{k: 'x'}", {"k": None})
        with pytest.raises(UnsafeExpressionError, match="keys must be strings"):
            evaluate_expression("{**m}", {"m": {1: "x"}})

    @pytest.mark.parametrize("value", [[1], "ab", 3, None])
    def test_rejects_non_dict_unpack(self, value):
        with pytest.raises(UnsafeExpressionError, match="only objects can be unpacked"):
            evaluate_expression("{**v}", {"v": value})

    def test_rejects_list_star_unpack(self):
        with pytest.raises(UnsafeExpressionError, match="list unpacking"):
            evaluate_expression("[*x]", {"x": [1, 2]})

    @pytest.mark.parametrize(
        "expression",
        [
            '{"k": len(x)}',
            '{"k": x[0]}',
            '{"k": [i for i in x]}',
            "{k: 1 for k in x}",
            '[__import__("os")]',
            '{"k": s.upper}',
            "{1, 2}",
            "(1, 2)",
        ],
    )
    def test_literals_keep_security_rejections(self, expression):
        with pytest.raises(UnsafeExpressionError):
            evaluate_expression(expression, {"x": [1], "s": "hi"})

    def test_size_boundary(self):
        at_limit = {str(i): i for i in range(100_000)}
        assert len(evaluate_expression("{**m}", {"m": at_limit})) == 100_000
        with pytest.raises(UnsafeExpressionError, match="object literal too large"):
            evaluate_expression('{**m, "extra": 1}', {"m": at_limit})
        with pytest.raises(UnsafeExpressionError, match="object literal too large"):
            evaluate_expression("{**m, **m}", {"m": at_limit})


class TestRejectedConstructs:
    def test_rejects_call(self):
        with pytest.raises(UnsafeExpressionError):
            evaluate_expression("len(x)", {"x": [1, 2]})

    def test_rejects_attribute_access_on_a_non_dict(self):
        # `.` walks nested dicts (see test_nested_attribute_access above) --
        # never Python's real `getattr`, so a non-dict base has nothing this
        # evaluator will resolve, closing off methods/dunders regardless of
        # what ends up in state.
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


class TestDoSBounds:
    def test_rejects_huge_exponent(self):
        with pytest.raises(UnsafeExpressionError, match="exponent too large"):
            evaluate_expression("9 ** 9 ** 9", {})

    def test_rejects_large_power_directly(self):
        with pytest.raises(UnsafeExpressionError, match="exponent too large"):
            evaluate_expression("2 ** 1001", {})

    def test_allows_small_power(self):
        assert evaluate_expression("2 ** 10", {}) == 1024

    def test_rejects_string_repetition_overflow(self):
        with pytest.raises(UnsafeExpressionError, match="sequence repetition result too long"):
            evaluate_expression("'x' * 100001", {})

    def test_allows_repetition_at_limit(self):
        assert len(evaluate_expression("'x' * 100000", {})) == 100_000

    def test_rejects_concatenation_overflow(self):
        # Doubling via `+` grows a sequence without ever using `*` on a large count.
        with pytest.raises(UnsafeExpressionError, match="sequence concatenation result too long"):
            evaluate_expression("s + s + s", {"s": "x" * 40_000})

    def test_allows_concatenation_and_numeric_add(self):
        assert evaluate_expression("a + b", {"a": [1], "b": [2]}) == [1, 2]
        assert evaluate_expression("10 ** 300 + 10 ** 300", {}) == 2 * 10**300

    def test_rejects_large_power_then_mult(self):
        with pytest.raises(UnsafeExpressionError, match="sequence repetition result too long"):
            evaluate_expression("'x' * 10 ** 9", {})

    def test_allows_small_sequence_repetition(self):
        assert evaluate_expression("'x' * 5", {}) == "xxxxx"

    def test_rejects_string_mod_formatting(self):
        with pytest.raises(UnsafeExpressionError, match="string formatting"):
            evaluate_expression("'hello %s' % 'world'", {})

    def test_allows_numeric_mod(self):
        assert evaluate_expression("10 % 3", {}) == 1

    def test_expression_length_boundary(self):
        at_limit = "1" + " + 1" * 499  # 1997 characters
        assert evaluate_expression(at_limit.ljust(2000), {}) == 500
        with pytest.raises(UnsafeExpressionError, match="expression too long"):
            evaluate_expression(at_limit.ljust(2001), {})
