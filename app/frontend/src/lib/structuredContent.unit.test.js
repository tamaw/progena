import { describe, expect, it } from 'vitest';
import { parseStructuredContent } from './structuredContent';

function nText(content, start, end) {
  return { type: 'text', content, start, end };
}

function nSection(tag, attributes, start, end, openEnd, closeStart, children) {
  return { type: 'section', tag, attributes, start, end, openEnd, closeStart, children };
}

function nHeading(level, title, start, end, children) {
  return { type: 'heading', level, start, end, title, children };
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

  // ─── Headings ───

  it('parses a level-2 heading', () => {
    const source = '## Plan';
    expect(parseStructuredContent(source)).toEqual([
      nHeading(2, 'Plan', 0, 7, []),
    ]);
  });

  it('parses a level-6 heading', () => {
    const source = '###### Deep';
    expect(parseStructuredContent(source)).toEqual([
      nHeading(6, 'Deep', 0, 11, []),
    ]);
  });

  it('treats H1 as plain text', () => {
    const source = '# Title';
    expect(parseStructuredContent(source)).toEqual([
      nText('# Title', 0, 7),
    ]);
  });

  it('treats single # without space as plain text', () => {
    const source = '#not-a-heading';
    expect(parseStructuredContent(source)).toEqual([
      nText('#not-a-heading', 0, 14),
    ]);
  });

  it('parses heading with content until EOF', () => {
    const source = '## Plan\nDo this.';
    expect(parseStructuredContent(source)).toEqual([
      nHeading(2, 'Plan', 0, 16, [
        nText('Do this.', 8, 16),
      ]),
    ]);
  });

  it('parses heading with content until next same-level heading', () => {
    const source = '## A\nbody A\n## B\nbody B';
    expect(parseStructuredContent(source)).toEqual([
      nHeading(2, 'A', 0, 12, [
        nText('body A\n', 5, 12),
      ]),
      nHeading(2, 'B', 12, 23, [
        nText('body B', 17, 23),
      ]),
    ]);
  });

  it('parses heading bounded by higher-level heading', () => {
    const source = '## A\n### B\nbody B\n## C';
    expect(parseStructuredContent(source)).toEqual([
      nHeading(2, 'A', 0, 18, [
        nHeading(3, 'B', 5, 18, [
          nText('body B\n', 11, 18),
        ]),
      ]),
      nHeading(2, 'C', 18, 22, []),
    ]);
  });

  it('parses deeply nested headings', () => {
    const source = '## A\n### B\n#### C\nbody\n### D';
    expect(parseStructuredContent(source)).toEqual([
      nHeading(2, 'A', 0, 28, [
        nHeading(3, 'B', 5, 23, [
          nHeading(4, 'C', 11, 23, [
            nText('body\n', 18, 23),
          ]),
        ]),
        nHeading(3, 'D', 23, 28, []),
      ]),
    ]);
  });

  it('preserves text before and after headings', () => {
    const source = 'before\n## Mid\nafter';
    expect(parseStructuredContent(source)).toEqual([
      nText('before\n', 0, 7),
      nHeading(2, 'Mid', 7, 19, [
        nText('after', 14, 19),
      ]),
    ]);
  });

  it('trims heading title', () => {
    const source = '##  Spaced Out  ';
    expect(parseStructuredContent(source)).toEqual([
      nHeading(2, 'Spaced Out', 0, 16, []),
    ]);
  });

  it('parses heading with CRLF', () => {
    const source = '## Plan\r\nbody';
    expect(parseStructuredContent(source)).toEqual([
      nHeading(2, 'Plan', 0, 13, [
        nText('body', 9, 13),
      ]),
    ]);
  });

  // ─── Allowed sections (questions/options/option) ───

  it('parses questions/options/option as sections', () => {
    const source = '<questions id="q1">Pick one:<options><option value="a">A</option><option value="b">B</option></options></questions>';
    expect(parseStructuredContent(source)).toEqual([
      nSection('questions', { id: 'q1' }, 0, 115, 19, 103, [
        nText('Pick one:', 19, 28),
        nSection('options', {}, 28, 103, 37, 93, [
          nSection('option', { value: 'a' }, 37, 65, 55, 56, [
            nText('A', 55, 56),
          ]),
          nSection('option', { value: 'b' }, 65, 93, 83, 84, [
            nText('B', 83, 84),
          ]),
        ]),
      ]),
    ]);
  });

  it('treats disallowed tags as literal text', () => {
    const source = '<plan>text</plan>';
    expect(parseStructuredContent(source)).toEqual([
      nText('<plan>text</plan>', 0, 17),
    ]);
  });

  it('treats nested disallowed tags as literal text', () => {
    const source = '<outer><inner>text</inner></outer>';
    expect(parseStructuredContent(source)).toEqual([
      nText('<outer><inner>text</inner></outer>', 0, 34),
    ]);
  });

  it('treats arbitrary tags with attributes as literal text', () => {
    const source = '<tag a="1">text</tag>';
    expect(parseStructuredContent(source)).toEqual([
      nText('<tag a="1">text</tag>', 0, 21),
    ]);
  });

  it('treats self-closing disallowed tags as text', () => {
    const source = '<br/>';
    expect(parseStructuredContent(source)).toEqual([
      nText('<br/>', 0, 5),
    ]);
  });

  it('treats void disallowed tags as text', () => {
    const source = '<br>';
    expect(parseStructuredContent(source)).toEqual([
      nText('<br>', 0, 4),
    ]);
  });

  it('treats malformed tag as literal text', () => {
    const source = '<123>not a tag</123>';
    expect(parseStructuredContent(source)).toEqual([
      nText('<123>not a tag</123>', 0, 20),
    ]);
  });

  it('treats unclosed allowed tag as literal text', () => {
    const source = '<questions>no end';
    expect(parseStructuredContent(source)).toEqual([
      nText('<questions>no end', 0, 17),
    ]);
  });

  it('treats mismatched close tag as literal text', () => {
    const source = '<options>text</questions>';
    expect(parseStructuredContent(source)).toEqual([
      nText('<options>text</questions>', 0, 25),
    ]);
  });

  it('treats unexpected close tag at top level as text', () => {
    const source = '</surprise>';
    expect(parseStructuredContent(source)).toEqual([
      nText('</surprise>', 0, 11),
    ]);
  });

  it('preserves text before and after allowed sections', () => {
    const source = 'before<questions>inside</questions>after';
    expect(parseStructuredContent(source)).toEqual([
      nText('before', 0, 6),
      nSection('questions', {}, 6, 35, 17, 23, [
        nText('inside', 17, 23),
      ]),
      nText('after', 35, 40),
    ]);
  });

  it('handles adjacent allowed sections', () => {
    const source = '<questions>1</questions><options>2</options>';
    expect(parseStructuredContent(source)).toEqual([
      nSection('questions', {}, 0, 24, 11, 12, [
        nText('1', 11, 12),
      ]),
      nSection('options', {}, 24, 44, 33, 34, [
        nText('2', 33, 34),
      ]),
    ]);
  });

  it('handles empty allowed section', () => {
    const source = '<options></options>';
    expect(parseStructuredContent(source)).toEqual([
      nSection('options', {}, 0, 19, 9, 9, []),
    ]);
  });

  // ─── Headings inside allowed sections ───

  it('parses headings inside questions sections', () => {
    const source = '<questions>\n## Q1\nYes or no?\n</questions>';
    expect(parseStructuredContent(source)).toEqual([
      nSection('questions', {}, 0, 41, 11, 29, [
        nText('\n', 11, 12),
        nHeading(2, 'Q1', 12, 29, [
          nText('Yes or no?\n', 18, 29),
        ]),
      ]),
    ]);
  });

  // ─── Allowed sections inside headings ───

  it('parses questions section inside heading', () => {
    const source = '## Plan\n<questions>Pick one:<options><option value="a">A</option></options></questions>';
    expect(parseStructuredContent(source)).toEqual([
      nHeading(2, 'Plan', 0, 87, [
        nSection('questions', {}, 8, 87, 19, 75, [
          nText('Pick one:', 19, 28),
          nSection('options', {}, 28, 75, 37, 65, [
            nSection('option', { value: 'a' }, 37, 65, 55, 56, [
              nText('A', 55, 56),
            ]),
          ]),
        ]),
      ]),
    ]);
  });

  // ─── Code fences and inline code ───

  it('leaves fenced code blocks as literal text', () => {
    const source = '```html\n<div>hello</div>\n```';
    expect(parseStructuredContent(source)).toEqual([
      nText('```html\n<div>hello</div>\n```', 0, 28),
    ]);
  });

  it('keeps tilde-fenced tag examples literal while parsing the next section', () => {
    const source = '~~~~xml\n<plan>example</plan>\n~~~~\n<questions>real</questions>';
    const nodes = parseStructuredContent(source);
    expect(nodes.filter((node) => node.type === 'section').map((node) => node.tag)).toEqual(['questions']);
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
    const source = '<questions>```\n<fake>code</fake>\n```\n</questions>';
    expect(parseStructuredContent(source)).toEqual([
      nSection('questions', {}, 0, 49, 11, 37, [
        nText('```\n<fake>code</fake>\n```\n', 11, 37),
      ]),
    ]);
  });

  it('leaves tags inside inline code as text within a section', () => {
    const source = '<questions>`<fake>code</fake>`</questions>';
    expect(parseStructuredContent(source)).toEqual([
      nSection('questions', {}, 0, 42, 11, 30, [
        nText('`<fake>code</fake>`', 11, 30),
      ]),
    ]);
  });

  it('leaves heading syntax inside code fence as literal text', () => {
    const source = '```\n## Not a heading\n```';
    expect(parseStructuredContent(source)).toEqual([
      nText('```\n## Not a heading\n```', 0, 24),
    ]);
  });

  it('leaves heading syntax inside inline code as literal text', () => {
    const source = '`## Not a heading`';
    expect(parseStructuredContent(source)).toEqual([
      nText('`## Not a heading`', 0, 18),
    ]);
  });

  it('leaves double-backtick inline code as literal text', () => {
    const source = '``<div>``';
    expect(parseStructuredContent(source)).toEqual([
      nText('``<div>``', 0, 9),
    ]);
  });

  it('leaves double-backtick inline code as text within a section', () => {
    const source = '<questions>``<inner>``</questions>';
    expect(parseStructuredContent(source)).toEqual([
      nSection('questions', {}, 0, 34, 11, 22, [
        nText('``<inner>``', 11, 22),
      ]),
    ]);
  });

  it('recognises CRLF in fenced code block closing line', () => {
    const source = '```\r\n<tag>hello</tag>\r\n```\r\n';
    expect(parseStructuredContent(source)).toEqual([
      nText('```\r\n<tag>hello</tag>\r\n```\r\n', 0, 28),
    ]);
  });

  it('recognises trailing whitespace in fenced code block closing line', () => {
    const source = '```\n<tag>hello</tag>\n```   \n';
    expect(parseStructuredContent(source)).toEqual([
      nText('```\n<tag>hello</tag>\n```   \n', 0, 28),
    ]);
  });

  it('recognises CRLF and trailing whitespace in fenced code block closing line', () => {
    const source = '```\r\n<tag>hello</tag>\r\n```   \r\n';
    expect(parseStructuredContent(source)).toEqual([
      nText('```\r\n<tag>hello</tag>\r\n```   \r\n', 0, 31),
    ]);
  });

  it('parses real tags after a valid fenced code block', () => {
    const source = '```\n<code>inside</code>\n```\n<questions>outside</questions>';
    expect(parseStructuredContent(source)).toEqual([
      nText('```\n<code>inside</code>\n```\n', 0, 28),
      nSection('questions', {}, 28, 58, 39, 46, [
        nText('outside', 39, 46),
      ]),
    ]);
  });

  // ─── Edge cases ───

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

  it('handles mixed content with headings and text', () => {
    const source = 'Hello\n## User\nhow are\n### Status\nyou\n? today';
    expect(parseStructuredContent(source)).toEqual([
      nText('Hello\n', 0, 6),
      nHeading(2, 'User', 6, 44, [
        nText('how are\n', 14, 22),
        nHeading(3, 'Status', 22, 44, [
          nText('you\n? today', 33, 44),
        ]),
      ]),
    ]);
  });

  it('parses question/options/option with heading nearby', () => {
    const source = '## Intro\n<questions>Q</questions>\n## Outro';
    expect(parseStructuredContent(source)).toEqual([
      nHeading(2, 'Intro', 0, 34, [
        nSection('questions', {}, 9, 33, 20, 21, [
          nText('Q', 20, 21),
        ]),
        nText('\n', 33, 34),
      ]),
      nHeading(2, 'Outro', 34, 42, []),
    ]);
  });

  it('recognises heading after disallowed tag text', () => {
    const source = '<div>text</div>\n## Real Heading';
    expect(parseStructuredContent(source)).toEqual([
      nText('<div>text</div>\n', 0, 16),
      nHeading(2, 'Real Heading', 16, 31, []),
    ]);
  });

  it('keeps duplicate tag-like text independently', () => {
    const source = '<q id="1">A</q><q id="2">B</q>';
    expect(parseStructuredContent(source)).toEqual([
      nText('<q id="1">A</q><q id="2">B</q>', 0, 30),
    ]);
  });

  it('preserves a single # as text even at line start', () => {
    const source = '#\n## Real';
    expect(parseStructuredContent(source)).toEqual([
      nText('#\n', 0, 2),
      nHeading(2, 'Real', 2, 9, []),
    ]);
  });

  it('parses heading with seven hashes as text', () => {
    const source = '####### Too many';
    expect(parseStructuredContent(source)).toEqual([
      nText('####### Too many', 0, 16),
    ]);
  });
});
