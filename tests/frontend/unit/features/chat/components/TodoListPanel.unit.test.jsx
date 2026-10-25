// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import TodoListPanel from '@frontend/features/chat/components/TodoListPanel';

let host;
let root;

beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
});

afterEach(async () => {
  if (root) await act(async () => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});

describe('TodoListPanel', () => {
  it('names the standalone plan checklist and expands it from its summary', async () => {
    await act(async () => root.render(
      <TodoListPanel todos={[
        { content: 'Inspect implementation', status: 'completed' },
        { content: 'Write regression test', status: 'in_progress' },
      ]} />,
    ));

    const details = host.querySelector('details');
    const summary = details.querySelector('summary');
    expect(summary.getAttribute('aria-label')).toBe('Plan checklist, 1 of 2 completed');
    expect(details.open).toBe(false);

    await act(async () => summary.click());

    expect(details.open).toBe(true);
    expect(host.textContent).toContain('Inspect implementation');
    expect(host.textContent).toContain('Write regression test');
  });
});
