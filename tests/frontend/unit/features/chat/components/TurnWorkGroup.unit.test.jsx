// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import TurnWorkGroup from '@frontend/features/chat/components/TurnWorkGroup';

vi.mock('react-redux', () => ({
  useSelector: (selector) => selector({ uiData: { preferences: { agentName: 'Progena' } } }),
}));

let host;
let root;

beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  vi.stubGlobal('React', React);
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} });
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
});

afterEach(async () => {
  if (root) await act(async () => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});

function tool(id, eventType, callId, timestamp) {
  return {
    kind: 'stream_event', id, timestamp,
    event: {
      id, eventType, timestamp, content: '',
      metadata: { tool_name: 'read_file', tool_call_id: callId, args_preview: { path: `/${callId}` } },
    },
  };
}

const groups = [{
  id: 'first', heading: 'Reading files', startedAt: 1_000,
  items: [tool('start', 'tool_start', 'read', 1_000), tool('result', 'tool_result', 'read', 2_000)],
}];

function render(props = {}) {
  return act(async () => root.render(
    <TurnWorkGroup groups={groups} workItems={groups[0].items} endedAt={5_000} responseStatus="completed" {...props} />,
  ));
}

describe('TurnWorkGroup', () => {
  it('uses a keyboard-operable outer disclosure and leaves detailed groups collapsed', async () => {
    await render();
    const toggle = host.querySelector('button[aria-expanded]');
    expect(toggle).not.toBeNull();
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    expect(host.querySelector('.work-group__toggle')).toBeNull();

    await act(async () => toggle.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })));
    expect(toggle.getAttribute('aria-expanded')).toBe('true');
    expect(host.querySelector('.work-group__toggle').getAttribute('aria-expanded')).toBe('false');
    expect(host.querySelector('.tool-call-entry')).toBeNull();

    await act(async () => toggle.dispatchEvent(new KeyboardEvent('keydown', { key: ' ', bubbles: true })));
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
  });

  it('shows headless tool rows directly after opening the turn disclosure', async () => {
    const headlessGroups = [{
      id: 'headless', heading: null, startedAt: 1_000,
      items: [tool('start', 'tool_start', 'read', 1_000), tool('result', 'tool_result', 'read', 2_000)],
    }];
    await render({ groups: headlessGroups, workItems: headlessGroups[0].items });

    const toggle = host.querySelector('button[aria-expanded]');
    await act(async () => toggle.click());

    expect(host.querySelector('.work-group__toggle')).toBeNull();
    expect(host.querySelectorAll('.tool-call-entry')).toHaveLength(1);
    expect(host.textContent.match(/Worked for 4s/g)).toHaveLength(1);
  });

  it.each([
    ['completed', 'Worked for 4s'],
    ['failed', 'Work failed'],
    ['cancelled', 'Work cancelled'],
    ['interrupted', 'Work interrupted'],
  ])('collapses on %s and reports its explicit terminal status', async (responseStatus, title) => {
    await render({ active: true, responseStatus });
    const toggle = host.querySelector('button[aria-expanded]');
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    expect(host.textContent).toContain(title);
  });

  it('auto-opens only while running, then collapses when the terminal event arrives', async () => {
    await render({ active: true, responseStatus: 'running' });
    const toggle = host.querySelector('button[aria-expanded]');
    expect(toggle.getAttribute('aria-expanded')).toBe('true');

    await render({ active: true, responseStatus: 'completed' });
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
  });

  it('keeps tool failure evidence without calling a completed response failed', async () => {
    const failedGroups = [{
      ...groups[0],
      items: [tool('start', 'tool_start', 'read', 1_000), tool('error', 'tool_error', 'read', 2_000)],
    }];
    await render({ groups: failedGroups, workItems: failedGroups[0].items });
    expect(host.textContent).toContain('Worked for 4s');
    expect(host.textContent).toContain('1 failed');
    expect(host.textContent).not.toContain('Work failed');
  });

  it('keeps unknown historical work neutral instead of inventing completion', async () => {
    await render({ responseStatus: undefined });
    expect(host.textContent).toContain('Work');
    expect(host.textContent).not.toContain('Worked for');
  });
});
