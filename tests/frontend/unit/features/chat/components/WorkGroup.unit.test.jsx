import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import WorkGroup from '@frontend/features/chat/components/WorkGroup';

vi.mock('react-redux', () => ({
  useSelector: (selector) => selector({ uiData: { preferences: { agentName: 'Progena' } } }),
}));

if (typeof globalThis.React === 'undefined') {
  globalThis.React = React;
}

function tool(id, eventType, callId, extra = {}) {
  return {
    kind: 'stream_event',
    id,
    timestamp: extra.timestamp,
    event: {
      id,
      eventType,
      content: '',
      timestamp: extra.timestamp,
      metadata: { tool_name: 'read_file', tool_call_id: callId, args_preview: { path: `/${callId}` }, ...extra.metadata },
    },
  };
}

const items = [
  tool('1', 'tool_start', 'a', { timestamp: 1_000 }),
  tool('2', 'tool_start', 'b', { timestamp: 1_100 }),
  tool('3', 'tool_result', 'b', { timestamp: 1_200 }),
  tool('4', 'tool_start', 'c', { timestamp: 1_300 }),
  tool('5', 'tool_result', 'c', { timestamp: 1_400 }),
  tool('6', 'tool_start', 'd', { timestamp: 1_500 }),
  tool('7', 'tool_error', 'd', { timestamp: 1_600 }),
  tool('8', 'tool_start', 'e', { timestamp: 1_700 }),
  tool('9', 'tool_result', 'e', { timestamp: 1_800 }),
];

describe('WorkGroup', () => {
  it('collapses finished work to a summary line with no individual rows', () => {
    const html = renderToStaticMarkup(React.createElement(WorkGroup, { items, active: false, endedAt: 9_000 }));
    expect(html).toContain('Worked for 8s');
    expect(html).toContain('5 tools');
    expect(html).toContain('1 failed');
    expect(html).not.toContain('/a');
    expect(html).not.toContain('work-step');
  });

  it('previews the latest steps while working, keeping an older running parallel call visible', () => {
    const html = renderToStaticMarkup(React.createElement(WorkGroup, { items, active: true }));
    expect(html).toContain('Generating…');
    // `a` started first and never finished: still shown although outside the latest three.
    expect(html).toContain('path: /a');
    expect(html).not.toContain('path: /b');
    expect(html).toContain('path: /c');
    expect(html).toContain('path: /d');
    expect(html).toContain('path: /e');
    expect(html).toContain('work-step--error');
  });

  it("does not repeat a headed group's reasoning as a preview step, since the header already shows it", () => {
    const reasoning = {
      kind: 'stream_event',
      id: 'r-s0',
      timestamp: 900,
      event: { id: 'r-s0', eventType: 'reasoning_delta', content: '**Reading files**\n\nLook first.', metadata: {}, timestamp: 900 },
    };
    const html = renderToStaticMarkup(React.createElement(WorkGroup, {
      heading: 'Reading files', items: [reasoning, ...items.slice(0, 2)], active: true,
    }));
    expect(html).toContain('Reading files');
    expect(html).toContain('path: /a');
    expect(html).not.toContain('work-step__badge--reasoning');
  });
});
