import { describe, expect, it } from 'vitest';
import { checkExpressionSyntax } from '@frontend/lib/restrictedExpression';

// Mirrors tests/progena/routines/test_expression.py's own accept/reject
// list -- this module exists purely to flag, client-side, what that backend
// evaluator would reject, so the two must agree on every case there.
describe('checkExpressionSyntax', () => {
  it('accepts a blank expression (the required-field gate handles that, not this)', () => {
    expect(checkExpressionSyntax('')).toEqual({ ok: true });
    expect(checkExpressionSyntax('   ')).toEqual({ ok: true });
  });

  it('accepts constants', () => {
    for (const expr of ['None', '42', '3.5', "'hi'", '"hi"', 'True', 'False']) {
      expect(checkExpressionSyntax(expr)).toEqual({ ok: true });
    }
  });

  it('accepts name lookups', () => {
    expect(checkExpressionSyntax('host')).toEqual({ ok: true });
  });

  it('accepts arithmetic with correct precedence', () => {
    for (const expr of ['1 + 2 * 3', '7 // 2', '7 % 2', '2 ** 3', '-2 ** 2']) {
      expect(checkExpressionSyntax(expr)).toEqual({ ok: true });
    }
  });

  it('accepts unary operators', () => {
    for (const expr of ['-x', '+x', 'not x']) {
      expect(checkExpressionSyntax(expr)).toEqual({ ok: true });
    }
  });

  it('accepts and/or, including short-circuit guard idioms', () => {
    for (const expr of ['a and b', 'a or b', 'x != 0 and 100 / x > 2']) {
      expect(checkExpressionSyntax(expr)).toEqual({ ok: true });
    }
  });

  it('accepts chained and in/not in comparisons', () => {
    for (const expr of ['0 <= x < 10', 'x in items', 'x not in items']) {
      expect(checkExpressionSyntax(expr)).toEqual({ ok: true });
    }
  });

  it('accepts a ternary and parenthesized grouping', () => {
    for (const expr of ['a if cond else b', '(a + b) * c', 'count < 3']) {
      expect(checkExpressionSyntax(expr)).toEqual({ ok: true });
    }
  });

  it('rejects a call, flagging the parenthesis', () => {
    const result = checkExpressionSyntax('len(x)');
    expect(result.ok).toBe(false);
    expect(result.message).toMatch(/function calls/);
  });

  it('accepts a dotted attribute chain', () => {
    expect(checkExpressionSyntax('parse.host')).toEqual({ ok: true });
    expect(checkExpressionSyntax('assess.repo.owner.name')).toEqual({ ok: true });
  });

  it('rejects a call after an attribute chain, flagging the parenthesis', () => {
    const result = checkExpressionSyntax('x.upper()');
    expect(result.ok).toBe(false);
    expect(result.message).toMatch(/function calls/);
  });

  it('rejects a dangling dot with nothing after it', () => {
    expect(checkExpressionSyntax('x.').ok).toBe(false);
  });

  it('rejects subscripting, flagging the bracket', () => {
    const result = checkExpressionSyntax('x[0]');
    expect(result.ok).toBe(false);
    expect(result.message).toMatch(/subscripting/);
  });

  it('rejects a list comprehension', () => {
    expect(checkExpressionSyntax('[i for i in x]').ok).toBe(false);
  });

  it('rejects lambda', () => {
    const result = checkExpressionSyntax('(lambda: 1)()');
    expect(result.ok).toBe(false);
    expect(result.message).toMatch(/lambda/);
  });

  it('rejects assignment', () => {
    expect(checkExpressionSyntax('x = 1').ok).toBe(false);
  });

  it('rejects is/is not, distinctly from a plain syntax error', () => {
    const result = checkExpressionSyntax('a is b');
    expect(result.ok).toBe(false);
    expect(result.message).toMatch(/'is' isn't supported/);
    expect(checkExpressionSyntax('a is not b').ok).toBe(false);
  });

  it('rejects an unterminated string, pointing at the opening quote', () => {
    const result = checkExpressionSyntax('x == "retry');
    expect(result.ok).toBe(false);
    expect(result.start).toBe(5);
  });

  it('rejects a dangling boolean operator', () => {
    expect(checkExpressionSyntax('a and').ok).toBe(false);
  });

  it('rejects an incomplete ternary missing else', () => {
    expect(checkExpressionSyntax('a if cond').ok).toBe(false);
  });

  it('reports a span usable for an inline underline', () => {
    const result = checkExpressionSyntax('count < ');
    expect(result.ok).toBe(false);
    expect(typeof result.start).toBe('number');
    expect(typeof result.end).toBe('number');
    expect(result.end).toBeGreaterThan(result.start);
  });
});
