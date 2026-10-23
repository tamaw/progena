import { describe, expect, it } from 'vitest';
import { runInNewContext } from 'node:vm';
import {
  buildStatePathSuggestions,
  filterStatePathSuggestions,
  getJavascriptAutocompleteContext,
  javascriptStatePathSuggestions,
} from '@frontend/features/routines/editor/graphFieldHelpers';

const nodes = [
  { id: 'producer', kind: 'prompt', output_key: 'market-data', config: {
    response_schema: { properties: { 'close.price': { type: 'number' } } },
  } },
  { id: 'consumer', kind: 'code' },
];
const suggestions = javascriptStatePathSuggestions(buildStatePathSuggestions(
  'consumer', nodes, [{ from: 'producer', to: 'consumer' }],
  { properties: { amount: { type: 'number' }, 'account name': { type: 'string' } } },
));

describe('JavaScript routine references', () => {
  it('inserts executable references using output keys and literal property segments', () => {
    const data = { 'market-data': { 'close.price': 42 } };
    const path = suggestions.find((item) => item.name === 'price').path;
    expect(runInNewContext(path, { input: data })).toBe(42);
    expect(suggestions.some((item) => item.path.includes('producer'))).toBe(false);
    const account = suggestions.find((item) => item.name === 'account name').path;
    expect(runInNewContext(account, { input: { 'account name': 'paper' } })).toBe('paper');
  });

  it('completes a reference in a JavaScript body without replacing surrounding code', () => {
    const source = 'return { total: input.am * 2 };';
    const caret = source.indexOf(' *');
    const context = getJavascriptAutocompleteContext(source, caret);
    const candidate = filterStatePathSuggestions(suggestions, context.query)[0];
    const inserted = source.slice(0, context.tokenStart) + candidate.path + source.slice(context.tokenEnd);
    expect(runInNewContext(`(function(input) { ${inserted} })({amount: 21})`).total).toBe(42);
  });

  it('replaces the rest of an identifier when accepting from its middle', () => {
    const source = 'return input.amount;';
    const caret = source.indexOf('amount') + 2;
    const context = getJavascriptAutocompleteContext(source, caret);
    expect(source.slice(context.tokenStart, context.tokenEnd)).toBe('input.amount');
  });

  it('matches single-quoted bracket access against canonical JavaScript suggestions', () => {
    const source = "return input['market-data'].";
    const context = getJavascriptAutocompleteContext(source, source.length);
    const candidates = filterStatePathSuggestions(suggestions, context.query);
    expect(candidates.map((item) => item.path)).toContain('input["market-data"]');
    const start = 'return input[';
    const bracket = getJavascriptAutocompleteContext(start, start.length);
    expect(filterStatePathSuggestions(suggestions, bracket.query).map((item) => item.path)).toContain('input.amount');
  });

  it.each(['return input["market-data"];', 'return input["market-data" ];'])(
    'does not duplicate a closing bracket in %s', (source) => {
      const caret = source.indexOf('"', source.indexOf('["') + 2) + 1;
      const context = getJavascriptAutocompleteContext(source, caret);
      const candidate = suggestions.find((item) => item.path === 'input["market-data"]');
      const inserted = source.slice(0, context.tokenStart) + candidate.path + source.slice(context.tokenEnd);
      expect(runInNewContext(`(function(input) { ${inserted} })({"market-data": 42})`)).toBe(42);
    },
  );

  it('suppresses completion before existing bracket contents rather than corrupting them', () => {
    const source = 'return input["market-data"];';
    expect(getJavascriptAutocompleteContext(source, source.indexOf('[') + 1)).toBeNull();
  });

  it.each(['const i', 'let inp', 'var input', 'function f(input', 'for (let i'])(
    'does not replace a binding with a state reference: %s',
    (source) => expect(getJavascriptAutocompleteContext(source, source.length)).toBeNull(),
  );

  it.each([
    'return "input.am', "return 'input.am", 'return /input.am/',
    '// input.am', '/* input.am', '/* input.am */',
    'return `input.am', 'return other.input.am', 'const inputValue = 1;',
  ])('does not suggest references in non-reference text: %s', (source) => {
    expect(getJavascriptAutocompleteContext(source, source.length)).toBeNull();
  });

  it('suggests references in template interpolation, but not template text', () => {
    const source = 'return `Price ${input.am';
    const context = getJavascriptAutocompleteContext(source, source.length);
    expect(filterStatePathSuggestions(suggestions, context.query)[0].path).toBe('input.amount');
  });
});
