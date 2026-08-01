// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const fakeMermaid = vi.hoisted(() => ({
  initialize: vi.fn(),
  run: vi.fn(() => Promise.resolve()),
}));

vi.mock('mermaid', () => ({ default: fakeMermaid }));

import { renderMermaidDiagrams } from './mermaid';

describe('renderMermaidDiagrams', () => {
  beforeEach(() => {
    delete document.documentElement.dataset.theme;
  });

  afterEach(() => {
    delete document.documentElement.dataset.theme;
  });

  it('does nothing when root is missing or has no mermaid blocks', async () => {
    await renderMermaidDiagrams(null);
    expect(fakeMermaid.run).not.toHaveBeenCalled();

    const empty = document.createElement('div');
    await renderMermaidDiagrams(empty);
    expect(fakeMermaid.run).not.toHaveBeenCalled();
  });

  it('runs mermaid against unprocessed pre.mermaid nodes', async () => {
    const root = document.createElement('div');
    root.innerHTML = '<pre class="mermaid">graph TD; A-->B;</pre>';

    await renderMermaidDiagrams(root);

    expect(fakeMermaid.run).toHaveBeenCalledTimes(1);
    const { nodes } = fakeMermaid.run.mock.calls[0][0];
    expect(nodes).toHaveLength(1);
    expect(nodes[0].textContent).toBe('graph TD; A-->B;');
  });

  it('skips nodes already marked data-processed', async () => {
    const root = document.createElement('div');
    root.innerHTML = '<pre class="mermaid" data-processed="true">graph TD; A-->B;</pre>';

    await renderMermaidDiagrams(root);

    expect(fakeMermaid.run).not.toHaveBeenCalled();
  });

  it('swallows a render failure so a malformed/incomplete diagram does not throw', async () => {
    fakeMermaid.run.mockRejectedValueOnce(new Error('parse error'));
    const root = document.createElement('div');
    root.innerHTML = '<pre class="mermaid">not valid mermaid</pre>';

    await expect(renderMermaidDiagrams(root)).resolves.toBeUndefined();
  });
});
