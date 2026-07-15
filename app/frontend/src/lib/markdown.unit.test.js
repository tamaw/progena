// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';
import { renderMarkdownToHtml } from './markdown';

function renderCodeFence(source) {
  const container = document.createElement('div');
  container.innerHTML = renderMarkdownToHtml(source);
  return container;
}

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
