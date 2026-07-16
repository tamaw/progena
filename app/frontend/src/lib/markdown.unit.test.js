// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest';

const highlighter = vi.hoisted(() => ({
  getLanguage: vi.fn((language) => language === 'js'),
  highlight: vi.fn((code) => ({ value: `<span class="hljs-keyword">${code}</span>` })),
  highlightAuto: vi.fn((code) => ({ value: code, language: '' })),
}));

vi.mock('highlight.js/lib/common', () => ({ default: highlighter }));

import { renderMarkdownToHtml } from './markdown';

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
