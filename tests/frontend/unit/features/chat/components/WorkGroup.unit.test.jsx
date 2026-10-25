// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import WorkGroup from '@frontend/features/chat/components/WorkGroup';

vi.mock('react-redux', () => ({
  useSelector: (selector) => selector({ uiData: { preferences: { agentName: 'Progena' } } }),
}));

let host;
let root;

beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  vi.stubGlobal('React', React);
  vi.stubGlobal('ResizeObserver', class {
    observe() {}
    disconnect() {}
  });
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
});

afterEach(async () => {
  if (root) await act(async () => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});

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
    expect(html).toContain('typing-word');
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

  it('shows provider reasoning detail after expanding its headed group', async () => {
    const reasoning = {
      kind: 'stream_event',
      id: 'provider-reasoning',
      timestamp: 900,
      event: {
        id: 'provider-reasoning',
        eventType: 'reasoning_summary',
        content: '**Inspect files**\n\nThis generated text must not replace provider metadata.',
        metadata: {
          summary: 'Provider summary',
          detail: 'Provider **reasoning detail**',
        },
        timestamp: 900,
      },
    };
    await act(async () => root.render(
      <WorkGroup heading="Inspect files" items={[reasoning]} active={false} endedAt={1_000} />,
    ));

    const toggle = host.querySelector('.work-group__toggle');
    await act(async () => toggle.click());

    expect(host.textContent).toContain('Provider summary');
    expect(host.textContent).toContain('Provider reasoning detail');
    expect(host.textContent).not.toContain('This generated text must not replace provider metadata.');
  });

  it('keeps generated reasoning body without repeating its heading', async () => {
    const reasoning = {
      kind: 'stream_event',
      id: 'generated-reasoning',
      timestamp: 900,
      event: {
        id: 'generated-reasoning',
        eventType: 'reasoning_delta',
        content: '**Reading files**\n\nFirst generated observation.\n\nSecond generated observation.',
        metadata: { source: 'stream_reasoning' },
        timestamp: 900,
      },
    };
    await act(async () => root.render(
      <WorkGroup heading="Reading files" items={[reasoning]} active={false} endedAt={1_000} />,
    ));

    await act(async () => host.querySelector('.work-group__toggle').click());

    expect(host.textContent).toContain('First generated observation.');
    expect(host.textContent).toContain('Second generated observation.');
    expect(host.textContent.match(/Reading files/g)).toHaveLength(1);
  });

  it('renders a failed write_todos call with its merged plan, commentary, input, and terminal error', async () => {
    const todos = [{ content: 'Keep this plan visible', status: 'pending' }];
    const writeTodosItems = [
      {
        kind: 'stream_event',
        id: 'todos-start',
        event: {
          id: 'todos-start',
          eventType: 'tool_start',
          content: '',
          metadata: {
            tool_name: 'write_todos',
            tool_call_id: 'todos-call',
            commentary: 'Saving the plan before continuing.',
            args_preview: { todos },
          },
        },
      },
      {
        kind: 'stream_event',
        id: 'todos-plan',
        event: {
          id: 'todos-plan',
          eventType: 'todos_update',
          content: '',
          metadata: { todos },
        },
      },
      {
        kind: 'stream_event',
        id: 'todos-result',
        event: {
          id: 'todos-result',
          eventType: 'tool_result',
          content: '',
          metadata: {
            tool_name: 'write_todos',
            tool_call_id: 'todos-call',
            success: false,
            error: 'Todo service rejected the update.',
          },
        },
      },
    ];
    await act(async () => root.render(
      <WorkGroup items={writeTodosItems} active={false} endedAt={1_000} />,
    ));

    await act(async () => host.querySelector('.work-group__toggle').click());

    expect(host.querySelector('.tool-call-entry--error')).not.toBeNull();
    expect(host.querySelector('.entry__badge--err')).not.toBeNull();
    expect(host.textContent).toContain('Keep this plan visible');
    expect(host.textContent).toContain('Saving the plan before continuing.');
    expect(host.textContent).toContain('Input');
    expect(host.textContent).toContain('Error');
    expect(host.textContent).toContain('Todo service rejected the update.');
  });
});
