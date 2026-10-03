// Client-side syntax check mirroring the whitelisted grammar
// `app/progenitor/routines/expression.py` accepts for `eval`/`condition`/`switch`
// config and `loop_guard` -- a small explicit subset of Python expression syntax,
// walked via `ast.parse(mode="eval")` plus a recursive node-type whitelist rather
// than a real interpreter. This is NOT a general Python parser: it exists only to
// flag the same expressions the backend would reject, in the browser, before a
// round trip, so `ExpressionField` can underline the offending span while typing.
// Dotted attribute access (`a.b.c`) is allowed -- the backend walks it as a
// dict-key lookup (`ast.Attribute` in `_eval_node`), never real `getattr`, so
// it only ever reaches into nested JSON-shaped state, not methods or dunders.
// Keep the grammar here in lockstep with `_ALLOWED_BINOPS`/`_ALLOWED_COMPARES`/
// `_eval_node` in expression.py -- see `tests/progenitor/routines/test_expression.py`
// for the backend's own accept/reject list this mirrors.

const KEYWORD_ATOMS = new Set(['None', 'True', 'False']);
const RESERVED_WORDS = new Set(['and', 'or', 'not', 'if', 'else', 'in', 'is', 'lambda']);
const TWO_CHAR_OPS = new Set(['==', '!=', '<=', '>=', '//', '**']);
const ONE_CHAR_OPS = new Set('+-*/%<>()!,.[]{}:&|^~='.split(''));

class ExprIssue extends Error {
  constructor(message, start, end) {
    super(message);
    this.start = start;
    this.end = end;
  }
}

// Exported for reuse by graphFieldHelpers.js's Expression-field autocomplete, which
// needs to know exactly what token (if any) precedes the caret -- string/
// number literals in particular, so it never offers state-path suggestions
// while the author is typing inside a quoted string.
export function tokenize(source) {
  const tokens = [];
  const n = source.length;
  let i = 0;
  const isDigit = (c) => c >= '0' && c <= '9';
  const isNameStart = (c) => /[A-Za-z_]/.test(c);
  const isNameChar = (c) => /[A-Za-z0-9_]/.test(c);
  while (i < n) {
    const c = source[i];
    if (c === ' ' || c === '\t' || c === '\n' || c === '\r') {
      i += 1;
      continue;
    }
    const start = i;
    if (isDigit(c) || (c === '.' && isDigit(source[i + 1]))) {
      let j = i;
      while (j < n && (isDigit(source[j]) || source[j] === '_')) j += 1;
      if (source[j] === '.') {
        j += 1;
        while (j < n && (isDigit(source[j]) || source[j] === '_')) j += 1;
      }
      if (source[j] === 'e' || source[j] === 'E') {
        let k = j + 1;
        if (source[k] === '+' || source[k] === '-') k += 1;
        if (isDigit(source[k])) {
          j = k;
          while (j < n && isDigit(source[j])) j += 1;
        }
      }
      tokens.push({ type: 'num', value: source.slice(i, j), start, end: j });
      i = j;
      continue;
    }
    if (c === "'" || c === '"') {
      const quote = c;
      let j = i + 1;
      let closed = false;
      while (j < n) {
        if (source[j] === '\\') {
          j += 2;
          continue;
        }
        if (source[j] === quote) {
          closed = true;
          j += 1;
          break;
        }
        if (source[j] === '\n') break;
        j += 1;
      }
      if (!closed) return { error: { message: 'unterminated string literal', start, end: n } };
      tokens.push({ type: 'str', value: source.slice(i, j), start, end: j });
      i = j;
      continue;
    }
    if (isNameStart(c)) {
      let j = i + 1;
      while (j < n && isNameChar(source[j])) j += 1;
      tokens.push({ type: 'name', value: source.slice(i, j), start, end: j });
      i = j;
      continue;
    }
    const two = source.slice(i, i + 2);
    if (TWO_CHAR_OPS.has(two)) {
      tokens.push({ type: 'op', value: two, start, end: i + 2 });
      i += 2;
      continue;
    }
    if (ONE_CHAR_OPS.has(c)) {
      tokens.push({ type: 'op', value: c, start, end: i + 1 });
      i += 1;
      continue;
    }
    return { error: { message: `unexpected character '${c}'`, start, end: i + 1 } };
  }
  tokens.push({ type: 'eof', value: '', start: n, end: n });
  return { tokens };
}

// Recursive-descent over exactly the grammar `_eval_node` walks: ternary (IfExp) >
// or/and (BoolOp) > not (UnaryOp) > comparison (chained Compare) > +- (BinOp) >
// */ // % (BinOp) > unary +- > ** (BinOp, right-assoc) > atom. Doesn't build a
// tree -- only validates -- so each level just consumes tokens and throws
// `ExprIssue` at the first construct outside the whitelist.
function parse(tokens) {
  let pos = 0;
  const peek = () => tokens[pos];
  const isName = (val) => peek().type === 'name' && peek().value === val;
  const isOp = (val) => peek().type === 'op' && peek().value === val;
  const advance = () => tokens[pos++];

  function parseTernary() {
    parseOr();
    if (isName('if')) {
      advance();
      parseOr();
      if (!isName('else')) {
        const tok = peek();
        throw new ExprIssue("expected 'else' to complete the ternary", tok.start, tok.end);
      }
      advance();
      parseTernary();
    }
  }

  function parseOr() {
    parseAnd();
    while (isName('or')) {
      advance();
      parseAnd();
    }
  }

  function parseAnd() {
    parseNot();
    while (isName('and')) {
      advance();
      parseNot();
    }
  }

  function parseNot() {
    if (isName('not')) {
      advance();
      parseNot();
      return;
    }
    parseComparison();
  }

  function parseComparison() {
    parseArith();
    for (;;) {
      const tok = peek();
      if (tok.type === 'op' && ['==', '!=', '<', '<=', '>', '>='].includes(tok.value)) {
        advance();
        parseArith();
        continue;
      }
      if (tok.type === 'name' && tok.value === 'in') {
        advance();
        parseArith();
        continue;
      }
      if (tok.type === 'name' && tok.value === 'not') {
        const save = pos;
        advance();
        if (isName('in')) {
          advance();
          parseArith();
          continue;
        }
        pos = save;
        break;
      }
      if (tok.type === 'name' && tok.value === 'is') {
        advance();
        let end = tok.end;
        if (isName('not')) {
          advance();
          end = tokens[pos - 1].end;
        }
        parseArith();
        throw new ExprIssue("'is' isn't supported here (use ==, !=, <, <=, >, >=, in, or not in)", tok.start, end);
      }
      break;
    }
  }

  function parseArith() {
    parseTerm();
    while (isOp('+') || isOp('-')) {
      advance();
      parseTerm();
    }
  }

  function parseTerm() {
    parseUnary();
    while (isOp('*') || isOp('/') || isOp('//') || isOp('%')) {
      advance();
      parseUnary();
    }
  }

  function parseUnary() {
    if (isOp('+') || isOp('-')) {
      advance();
      parseUnary();
      return;
    }
    parsePower();
  }

  function parsePower() {
    parseAtomWithPostfixCheck();
    if (isOp('**')) {
      advance();
      parseUnary();
    }
  }

  // `.` chains freely (`a.b.c`) -- matches expression.py's `ast.Attribute`
  // handling, a dict-only walk (never real Python `getattr`) added after
  // this checker was first written. Still rejects calls/subscripts
  // immediately, whether right after the atom itself or after any `.name`
  // in the chain (`a.b()`, `a[0]`).
  function parseAtomWithPostfixCheck() {
    parseAtom();
    for (;;) {
      const tok = peek();
      if (tok.type === 'op' && tok.value === '.') {
        advance();
        const attr = peek();
        if (attr.type !== 'name') {
          throw new ExprIssue("expected an attribute name after '.'", attr.start, Math.max(attr.end, attr.start + 1));
        }
        advance();
        continue;
      }
      if (tok.type === 'op' && (tok.value === '(' || tok.value === '[')) {
        const kind = tok.value === '(' ? 'function calls' : 'subscripting';
        throw new ExprIssue(`${kind} aren't supported in restricted expressions`, tok.start, tok.end);
      }
      break;
    }
  }

  function parseAtom() {
    const tok = peek();
    if (tok.type === 'num' || tok.type === 'str') {
      advance();
      return;
    }
    if (tok.type === 'name') {
      if (KEYWORD_ATOMS.has(tok.value)) {
        advance();
        return;
      }
      if (tok.value === 'lambda') {
        throw new ExprIssue("lambda expressions aren't supported in restricted expressions", tok.start, tok.end);
      }
      if (RESERVED_WORDS.has(tok.value)) {
        throw new ExprIssue(`unexpected keyword '${tok.value}'`, tok.start, tok.end);
      }
      advance();
      return;
    }
    if (tok.type === 'op' && tok.value === '(') {
      advance();
      parseTernary();
      if (!isOp(')')) {
        const t = peek();
        throw new ExprIssue("expected ')'", t.start, Math.max(t.end, t.start + 1));
      }
      advance();
      return;
    }
    if (tok.type === 'op' && tok.value === '[') {
      throw new ExprIssue("list literals aren't supported in restricted expressions", tok.start, tok.end);
    }
    if (tok.type === 'op' && tok.value === '{') {
      throw new ExprIssue("dict/set literals aren't supported in restricted expressions", tok.start, tok.end);
    }
    throw new ExprIssue(
      tok.type === 'eof' ? 'expression ends unexpectedly' : `unexpected '${tok.value}'`,
      tok.start,
      Math.max(tok.end, tok.start + 1),
    );
  }

  parseTernary();
  if (peek().type !== 'eof') {
    const tok = peek();
    throw new ExprIssue(`unexpected '${tok.value}'`, tok.start, Math.max(tok.end, tok.start + 1));
  }
}

// Checks `source` against the restricted grammar. A blank/whitespace-only
// expression is reported as ok -- that's the node's own "required" gate's job,
// not a syntax error. Returns `{ ok: true }` or `{ ok: false, message, start,
// end }` for the first offending token (character offsets into `source`).
export function checkExpressionSyntax(source) {
  const text = source ?? '';
  if (!text.trim()) return { ok: true };
  const tokenized = tokenize(text);
  if (tokenized.error) {
    return { ok: false, message: tokenized.error.message, start: tokenized.error.start, end: tokenized.error.end };
  }
  try {
    parse(tokenized.tokens);
    return { ok: true };
  } catch (err) {
    if (err instanceof ExprIssue) {
      return { ok: false, message: err.message, start: err.start, end: Math.max(err.end, err.start + 1) };
    }
    throw err;
  }
}
