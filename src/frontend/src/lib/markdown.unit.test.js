// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest';

const highlighter = vi.hoisted(() => ({
  getLanguage: vi.fn((language) => language === 'js'),
  highlight: vi.fn((code) => ({ value: `<span class="hljs-keyword">${code}</span>` })),
  highlightAuto: vi.fn((code) => ({ value: code, language: '' })),
}));

vi.mock('highlight.js/lib/common', () => ({ default: highlighter }));

import { renderMarkdownToHtml, stripYamlFrontmatter } from './markdown';

function renderCodeFence(source) {
  const container = document.createElement('div');
  container.innerHTML = renderMarkdownToHtml(source);
  return container;
}

describe('renderMarkdownToHtml fenced-code highlighting budget', () => {
  beforeEach(() => {
    highlighter.getLanguage.mockClear();
    highlighter.highlight.mockClear();
    highlighter.highlightAuto.mockClear();
  });

  it('keeps a code block larger than 12 KiB escaped and visible without invoking highlighter grammars', () => {
    const largeCode = 'const payload = "<tag>";\n'.repeat(600);
    const container = renderCodeFence(['```js', largeCode, '```'].join('\n'));
    const code = container.querySelector('.code-block code');

    expect(code).not.toBeNull();
    expect(code?.className).toContain('language-js');
    expect(code?.textContent).toContain('const payload = "<tag>";');
    expect(code?.innerHTML).toContain('&lt;tag&gt;');
    expect(highlighter.highlight).not.toHaveBeenCalled();
    expect(highlighter.highlightAuto).not.toHaveBeenCalled();
  });

  it('keeps named small fences syntax highlighted', () => {
    const container = renderCodeFence('```js\nconst answer = 42;\n```');

    expect(highlighter.highlight).toHaveBeenCalledOnce();
    expect(highlighter.highlight).toHaveBeenCalledWith('const answer = 42;', { language: 'js' });
    expect(container.querySelector('.code-block code .hljs-keyword')?.textContent).toBe('const answer = 42;');
  });
});

describe('renderMarkdownToHtml mermaid fences', () => {
  it('renders a ```mermaid``` fence as a pre.mermaid block with raw escaped source', () => {
    const container = renderCodeFence([
      '```mermaid',
      'graph TD;',
      '  A-->B;',
      '```',
    ].join('\n'));

    const mermaidBlock = container.querySelector('pre.mermaid');
    expect(mermaidBlock).not.toBeNull();
    expect(mermaidBlock?.textContent).toBe('graph TD;\n  A-->B;');
    expect(container.querySelector('.code-block')).toBeNull();
    expect(highlighter.highlight).not.toHaveBeenCalled();
    expect(highlighter.highlightAuto).not.toHaveBeenCalled();
  });
});

describe('renderMarkdownToHtml fenced text blocks', () => {
  it('keeps heading and list syntax literal inside a fenced text block', () => {
    const container = renderCodeFence([
      '```text',
      '## Literal heading',
      '- literal item',
      '```',
    ].join('\n'));

    const codeBlock = container.querySelector('.code-block');
    expect(codeBlock).not.toBeNull();
    expect(codeBlock.querySelector('code')?.textContent).toBe('## Literal heading\n- literal item');
    expect(codeBlock.querySelector('.md-heading, li, ul, ol')).toBeNull();
  });

  it('preserves a response-shaped fenced text payload as code rather than markdown', () => {
    const container = renderCodeFence([
      '```text',
      '### Response',
      '- Status: complete',
      '- Result: retained',
      '```',
    ].join('\n'));

    const codeBlock = container.querySelector('.code-block');
    expect(codeBlock).not.toBeNull();
    expect(codeBlock.querySelector('code')?.textContent).toBe(
      '### Response\n- Status: complete\n- Result: retained',
    );
    expect(container.querySelectorAll('.md-heading, li.md-li, ul.md-ul').length).toBe(0);
  });
});

describe('stripYamlFrontmatter', () => {
  it.each([
    {
      name: 'LF-delimited frontmatter',
      source: '---\ntitle: Skills preview\ntags:\n  - markdown\n---\n# Skills heading\n\nBody text.',
      expected: '# Skills heading\n\nBody text.',
    },
    {
      name: 'CRLF-delimited frontmatter',
      source: '---\r\ntitle: Skills preview\r\ntags:\r\n  - markdown\r\n---\r\n# Skills heading\r\n\r\nBody text.',
      expected: '# Skills heading\r\n\r\nBody text.',
    },
  ])('removes one leading YAML block with $name while preserving the markdown body', ({ source, expected }) => {
    const stripped = stripYamlFrontmatter(source);

    expect(stripped).toBe(expected);

    const container = document.createElement('div');
    container.innerHTML = renderMarkdownToHtml(stripped);
    expect(container.querySelector('.md-heading')?.textContent).toBe('Skills heading');
  });

  it('leaves markdown without frontmatter unchanged', () => {
    const markdown = '# Existing heading\n\nBody text.';

    expect(stripYamlFrontmatter(markdown)).toBe(markdown);
  });

  it('leaves an unterminated leading delimiter unchanged', () => {
    const markdown = '---\ntitle: Missing closing delimiter\n# Heading that is still body text';

    expect(stripYamlFrontmatter(markdown)).toBe(markdown);
  });
});
