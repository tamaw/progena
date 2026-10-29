// @vitest-environment jsdom
import React, { act, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import RunTimeline from '@frontend/features/runs/RunTimeline';

const trace = {
  status: 'completed',
  started_at: '2026-10-29T00:00:00Z',
  completed_at: '2026-10-29T00:00:10Z',
  spans: [
    { id: 'run', kind: 'run', name: 'Run', start: '2026-10-29T00:00:00Z', end: '2026-10-29T00:00:10Z' },
    { id: 'parent', parent_id: 'run', kind: 'agent', name: 'Parent', start: '2026-10-29T00:00:01Z', end: '2026-10-29T00:00:09Z' },
    { id: 'tool-one', parent_id: 'parent', kind: 'tool', name: 'Tool one', start: '2026-10-29T00:00:02Z', end: '2026-10-29T00:00:03Z' },
    { id: 'tool-two', parent_id: 'parent', kind: 'tool', name: 'Tool two', start: '2026-10-29T00:00:04Z', end: '2026-10-29T00:00:05Z' },
  ],
};

function TimelineHarness({ mounted }) {
  const [inspection, setInspection] = useState(null);
  return mounted ? <RunTimeline
    trace={trace}
    bounds={{ start: Date.parse(trace.started_at), end: Date.parse(trace.completed_at), duration: 10_000, isLive: false }}
    now={Date.parse(trace.completed_at)}
    inspection={inspection}
    onInspectionChange={setInspection}
  /> : null;
}

describe('RunTimeline inspection state', () => {
  let host;
  let root;

  beforeEach(() => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    vi.stubGlobal('React', React);
    host = document.createElement('div');
    document.body.append(host);
    root = createRoot(host);
  });

  afterEach(async () => {
    if (root) await act(async () => root.unmount());
    host.remove();
    vi.unstubAllGlobals();
  });

  it('restores collapsed branches, call selection, and details after a virtual row remount', async () => {
    await act(async () => root.render(<TimelineHarness mounted />));
    const toolTwo = host.querySelector('[aria-label^="Select Tool two"]');
    await act(async () => toolTwo.click());
    await act(async () => host.querySelector('[aria-label="Show details for Tool calls"]').click());
    await act(async () => host.querySelector('[aria-label="Collapse Parent"]').click());

    await act(async () => root.render(<TimelineHarness mounted={false} />));
    await act(async () => root.render(<TimelineHarness mounted />));
    expect(host.querySelector('[aria-label="Expand Parent"]')).not.toBeNull();

    await act(async () => host.querySelector('[aria-label="Expand Parent"]').click());
    expect(host.querySelector('[aria-label^="Select Tool two"]').getAttribute('aria-pressed')).toBe('true');
    expect(host.querySelector('[aria-label="Hide details for Tool calls"]')).not.toBeNull();
  });
});
