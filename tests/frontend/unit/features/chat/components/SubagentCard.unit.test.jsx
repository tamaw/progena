// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import SubagentCard from '@frontend/features/chat/components/SubagentCard';

let host;
let root;

beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
});

afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});

function subagentItem(overrides = {}) {
  return {
    id: 'subagent-1',
    kind: 'subagent',
    status: 'running',
    subagentName: 'repo-manager',
    entries: [],
    ...overrides,
  };
}

async function render(item) {
  await act(async () => root.render(<SubagentCard item={item} />));
}

async function toggle() {
  await act(async () => host.querySelector('button[aria-expanded]').click());
}

describe('SubagentCard', () => {
  it('reveals a persisted Markdown result after completion and can collapse it again', async () => {
    await render(subagentItem({
      status: 'success',
      resultPreview: '## Repository inspection\n\n- Found the branch.\n- Reviewed changes.\n\n```js\nconst answer = 42;\n```',
    }));
    expect(host.querySelector('.subagent-card__body')).toBeNull();

    await toggle();
    expect(host.querySelector('h2')?.textContent).toBe('Repository inspection');
    expect([...host.querySelectorAll('li')].map((node) => node.textContent)).toEqual([
      'Found the branch.', 'Reviewed changes.',
    ]);
    expect(host.querySelector('pre code')?.textContent).toContain('const answer = 42;');

    await toggle();
    expect(host.querySelector('.subagent-card__body')).toBeNull();
  });

  it('shows activity before any output, while collapsed, and removes it on completion', async () => {
    await render(subagentItem());
    expect(host.querySelector('[role="status"]')?.textContent).toContain('Working');
    expect(host.querySelector('.subagent-card__body')).toBeNull();

    await toggle();
    expect(host.querySelector('button').getAttribute('aria-expanded')).toBe('false');
    expect(host.querySelector('[role="status"]')?.textContent).toContain('Working');

    await render(subagentItem({ status: 'success', resultPreview: 'Inspection finished.' }));
    expect(host.querySelector('[role="status"]')).toBeNull();
    await toggle();
    expect(host.textContent).toContain('Inspection finished.');
  });

  it('keeps streaming code literal, then renders it as Markdown when a tool starts', async () => {
    const entry = { kind: 'message', id: 'm1', text: '```js\nconst answer = 42;\n```' };
    await render(subagentItem({ entries: [entry] }));
    expect(host.textContent).toContain(entry.text);
    expect(host.querySelector('pre code')).toBeNull();

    await render(subagentItem({ entries: [
      entry,
      { kind: 'tool', id: 't1', toolName: 'read_file', status: 'running' },
    ] }));
    expect(host.querySelector('pre code')?.textContent).toContain('const answer = 42;');
    expect(host.textContent).toContain('read_file');
    expect(host.querySelector('[role="status"]')).not.toBeNull();
  });

  it('exposes the full task in a keyboard-accessible styled tooltip without expanding the result', async () => {
    const taskSummary = 'Inspect the interrupted preparation and report repository status.';
    await render(subagentItem({ status: 'success', taskSummary, resultPreview: 'Done.' }));
    expect(host.querySelector('[title]')).toBeNull();

    await act(async () => host.querySelector('.subagent-card__header').focus());
    const tooltip = document.querySelector('[role="tooltip"]');
    expect(tooltip?.textContent).toBe(taskSummary);
    expect(host.querySelector('.subagent-card__body')).toBeNull();
    await act(async () => host.querySelector('.subagent-card__header').dispatchEvent(
      new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }),
    ));
    expect(document.querySelector('[role="tooltip"]')).toBeNull();
  });

  it('keeps a failed delegate error visible while its transcript is collapsed', async () => {
    await render(subagentItem({ status: 'error', error: 'Repository unavailable' }));
    expect(host.querySelector('.subagent-card__body')).toBeNull();
    expect(host.textContent).toContain('Repository unavailable');
    expect(host.querySelector('[role="status"]')).toBeNull();
  });
});
