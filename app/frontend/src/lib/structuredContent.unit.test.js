import { describe, expect, it } from 'vitest';
import { parseStructuredContent } from './structuredContent';

function nText(content, start, end) {
  return { type: 'text', content, start, end };
}

function nSection(tag, attributes, start, end, openEnd, closeStart, children) {
  return { type: 'section', tag, attributes, start, end, openEnd, closeStart, children };
}

describe('parseStructuredContent', () => {
  it('returns empty array for empty string', () => {
    expect(parseStructuredContent('')).toEqual([]);
  });

  it('returns single text node for plain text', () => {
    expect(parseStructuredContent('hello world')).toEqual([
      nText('hello world', 0, 11),
    ]);
  });

  it('parses a simple section', () => {
    const source = '<question>What is 2+2?</question>';
    expect(parseStructuredContent(source)).toEqual([
      nSection('question', {}, 0, 33, 10, 22, [
        nText('What is 2+2?', 10, 22),
      ]),
    ]);
  });

  it('parses nested sections', () => {
    const source = '<outer><inner>text</inner></outer>';
    expect(parseStructuredContent(source)).toEqual([
      nSection('outer', {}, 0, 34, 7, 26, [
        nSection('inner', {}, 7, 26, 14, 18, [
          nText('text', 14, 18),
        ]),
      ]),
    ]);
  });

  it('parses deeply nested sections', () => {
    const source = '<a><b><c>deep</c></b></a>';
    expect(parseStructuredContent(source)).toEqual([
      nSection('a', {}, 0, 25, 3, 21, [
        nSection('b', {}, 3, 21, 6, 17, [
          nSection('c', {}, 6, 17, 9, 13, [
            nText('deep', 9, 13),
          ]),
        ]),
      ]),
    ]);
  });

  it('keeps duplicate tag names independently addressable', () => {
    const source = '<q id="1">A</q><q id="2">B</q>';
    expect(parseStructuredContent(source)).toEqual([
      nSection('q', { id: '1' }, 0, 15, 10, 11, [
        nText('A', 10, 11),
      ]),
      nSection('q', { id: '2' }, 15, 30, 25, 26, [
        nText('B', 25, 26),
      ]),
    ]);
  });

  it('parses attributes with double quotes', () => {
    const source = '<question id="q1" type="math">content</question>';
    expect(parseStructuredContent(source)).toEqual([
      nSection('question', { id: 'q1', type: 'math' }, 0, 48, 30, 37, [
        nText('content', 30, 37),
      ]),
    ]);
  });

  it('parses attributes with single quotes', () => {
    const source = "<tag val='x'>text</tag>";
    expect(parseStructuredContent(source)).toEqual([
      nSection('tag', { val: 'x' }, 0, 23, 13, 17, [
        nText('text', 13, 17),
      ]),
    ]);
  });

  it('parses unquoted attributes', () => {
    const source = '<tag flag=value>text</tag>';
    expect(parseStructuredContent(source)).toEqual([
      nSection('tag', { flag: 'value' }, 0, 26, 16, 20, [
        nText('text', 16, 20),
      ]),
    ]);
  });

  it('parses boolean attributes without value', () => {
    const source = '<tag disabled>text</tag>';
    expect(parseStructuredContent(source)).toEqual([
      nSection('tag', { disabled: '' }, 0, 24, 14, 18, [
        nText('text', 14, 18),
      ]),
    ]);
  });

  it('treats self-closing tag as section with empty children', () => {
    const source = '<br/>';
    expect(parseStructuredContent(source)).toEqual([
      nSection('br', {}, 0, 5, 5, 5, []),
    ]);
  });

  it('treats void tag as self-closing', () => {
    const source = '<br>';
    expect(parseStructuredContent(source)).toEqual([
      nSection('br', {}, 0, 4, 4, 4, []),
    ]);
  });

  it('leaves fenced code blocks as literal text', () => {
    const source = '```html\n<div>hello</div>\n```';
    expect(parseStructuredContent(source)).toEqual([
      nText('```html\n<div>hello</div>\n```', 0, 28),
    ]);
  });

  it('keeps tilde-fenced tag examples literal while parsing the next section', () => {
    const source = '~~~~xml\n<plan>example</plan>\n~~~~\n<plan>real</plan>';
    const nodes = parseStructuredContent(source);
    expect(nodes.filter((node) => node.type === 'section').map((node) => node.tag)).toEqual(['plan']);
    expect(nodes[0].content).toContain('<plan>example</plan>');
    expect(nodes[1].children[0].content).toBe('real');
  });

  it('leaves inline backtick code as literal text', () => {
    const source = 'Use `<div>` tag';
    expect(parseStructuredContent(source)).toEqual([
      nText('Use `<div>` tag', 0, 15),
    ]);
  });

  it('leaves tags inside code fences as text', () => {
    const source = '<outer>```\n<inner>text</inner>\n```</outer>';
    expect(parseStructuredContent(source)).toEqual([
      nSection('outer', {}, 0, 42, 7, 34, [
        nText('```\n<inner>text</inner>\n```', 7, 34),
      ]),
    ]);
  });

  it('leaves tags inside inline code as text within a section', () => {
    const source = '<outer>`<inner>text</inner>`</outer>';
    expect(parseStructuredContent(source)).toEqual([
      nSection('outer', {}, 0, 36, 7, 28, [
        nText('`<inner>text</inner>`', 7, 28),
      ]),
    ]);
  });

  it('treats malformed tag as literal text', () => {
    const source = '<123>not a tag</123>';
    expect(parseStructuredContent(source)).toEqual([
      nText('<123>not a tag</123>', 0, 20),
    ]);
  });

  it('treats unclosed tag as literal text', () => {
    const source = '<unclosed>no end';
    expect(parseStructuredContent(source)).toEqual([
      nText('<unclosed>no end', 0, 16),
    ]);
  });

  it('treats mismatched close tag as literal text', () => {
    const source = '<a>text</b>';
    expect(parseStructuredContent(source)).toEqual([
      nText('<a>text</b>', 0, 11),
    ]);
  });

  it('treats unexpected close tag at top level as text', () => {
    const source = '</surprise>';
    expect(parseStructuredContent(source)).toEqual([
      nText('</surprise>', 0, 11),
    ]);
  });

  it('preserves text before and after sections', () => {
    const source = 'before<section>inside</section>after';
    expect(parseStructuredContent(source)).toEqual([
      nText('before', 0, 6),
      nSection('section', {}, 6, 31, 15, 21, [
        nText('inside', 15, 21),
      ]),
      nText('after', 31, 36),
    ]);
  });

  it('handles adjacent sections', () => {
    const source = '<a>1</a><b>2</b>';
    expect(parseStructuredContent(source)).toEqual([
      nSection('a', {}, 0, 8, 3, 4, [
        nText('1', 3, 4),
      ]),
      nSection('b', {}, 8, 16, 11, 12, [
        nText('2', 11, 12),
      ]),
    ]);
  });

  it('handles empty section', () => {
    const source = '<empty></empty>';
    expect(parseStructuredContent(source)).toEqual([
      nSection('empty', {}, 0, 15, 7, 7, []),
    ]);
  });

  it('handles multiple attributes with whitespace', () => {
    const source = '<tag  a="1"   b=\'2\'  c=3>text</tag>';
    expect(parseStructuredContent(source)).toEqual([
      nSection('tag', { a: '1', b: '2', c: '3' }, 0, 35, 25, 29, [
        nText('text', 25, 29),
      ]),
    ]);
  });

  it('handles mixed content with nested sections and text', () => {
    const source = 'Hello <user name="Ada">how are <status>you</status>?</user> today';
    expect(parseStructuredContent(source)).toEqual([
      nText('Hello ', 0, 6),
      nSection('user', { name: 'Ada' }, 6, 59, 23, 52, [
        nText('how are ', 23, 31),
        nSection('status', {}, 31, 51, 39, 42, [
          nText('you', 39, 42),
        ]),
        nText('?', 51, 52),
      ]),
      nText(' today', 59, 65),
    ]);
  });

  it('treats tag-like text in code fence inside section as text', () => {
    const source = '<section>```\n<fake>code</fake>\n```</section>';
    expect(parseStructuredContent(source)).toEqual([
      nSection('section', {}, 0, 44, 9, 34, [
        nText('```\n<fake>code</fake>\n```', 9, 34),
      ]),
    ]);
  });

  it('treats incomplete tag start as text', () => {
    const source = 'text<';
    expect(parseStructuredContent(source)).toEqual([
      nText('text<', 0, 5),
    ]);
  });

  it('treats lone less-than as text', () => {
    const source = 'a < b';
    expect(parseStructuredContent(source)).toEqual([
      nText('a < b', 0, 5),
    ]);
  });

  it('parses question/options/answer as ordinary sections', () => {
    const source = '<question id="q1">Pick one:</question><options><option value="a">A</option><option value="b">B</option></options><answer id="q1" />';
    expect(parseStructuredContent(source)).toEqual([
      nSection('question', { id: 'q1' }, 0, 38, 18, 27, [
        nText('Pick one:', 18, 27),
      ]),
      nSection('options', {}, 38, 113, 47, 103, [
        nSection('option', { value: 'a' }, 47, 75, 65, 66, [
          nText('A', 65, 66),
        ]),
        nSection('option', { value: 'b' }, 75, 103, 93, 94, [
          nText('B', 93, 94),
        ]),
      ]),
      nSection('answer', { id: 'q1' }, 113, 131, 131, 131, []),
    ]);
  });
});
