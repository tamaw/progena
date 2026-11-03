// @vitest-environment jsdom
import React, { act } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createRoot } from 'react-dom/client';
import StickyTodoProgress from '@frontend/features/chat/components/StickyTodoProgress';
import { createLiveAssistantTurnStore } from '@frontend/features/chat/models/liveAssistantTurnStore';

let host;
let root;

function turnWithTodos(todos, timestamp) {
  return {
    timelineItems: [{
      kind: 'stream_event',
      event: { eventType: 'todos_update', timestamp, metadata: { todos } },
    }],
  };
}

beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
});

afterEach(async () => {
  await act(async () => root?.unmount());
  host?.remove();
  vi.unstubAllGlobals();
});

describe('StickyTodoProgress', () => {
  it('locally follows the latest active plan and exposes its full checklist', async () => {
    const timestamp = '2026-11-03T00:00:00Z';
    const store = createLiveAssistantTurnStore(turnWithTodos([
      { content: 'Inspect implementation', status: 'completed' },
      { content: 'Write regression test', status: 'in_progress' },
    ], timestamp));
    await act(async () => root.render(<StickyTodoProgress liveTurnStore={store} isActive messages={[]} />));

    expect(host.textContent).toContain('1/2');
    expect(host.textContent).toContain('Write regression test');
    expect(host.querySelector('details').open).toBe(false);

    await act(async () => host.querySelector('summary').click());
    expect(host.textContent).toContain('Inspect implementation');

    await act(async () => store.set(turnWithTodos([
      { content: 'Inspect implementation', status: 'completed' },
      { content: 'Write regression test', status: 'completed' },
      { content: 'Report result', status: 'in_progress' },
    ])));
    expect(host.textContent).toContain('2/3');
    expect(host.textContent).toContain('Report result');
    await act(async () => store.set(turnWithTodos([
      { content: 'Inspect implementation', status: 'completed' },
      { content: 'Write regression test', status: 'completed' },
      { content: 'Report result', status: 'completed' },
    ])));
    expect(host.querySelector('details')).toBeNull();
  });


  it('uses the current persisted assistant plan while reconnecting an active turn', async () => {
    const store = createLiveAssistantTurnStore();
    const messages = [{
      role: 'assistant',
      id: 'active-message',
      streamEvents: [
        { id: 'todo-start', eventType: 'tool_start', timestamp: '2026-11-03T00:00:00Z', metadata: { tool_name: 'write_todos', tool_call_id: 'todos-1', args_preview: { todos: [{ content: 'Reconnect plan', status: 'in_progress' }] } } },
        { id: 'todo-result', eventType: 'tool_result', timestamp: '2026-11-03T00:00:01Z', metadata: { tool_name: 'write_todos', tool_call_id: 'todos-1' } },
      ],
    }];
    await act(async () => root.render(<StickyTodoProgress liveTurnStore={store} isActive messages={messages} />));
    expect(host.textContent).toContain('0/1');
    expect(host.textContent).toContain('Reconnect plan');

  });

  it('clears a replaced plan instead of resurrecting its earlier snapshot', async () => {
    const store = createLiveAssistantTurnStore(turnWithTodos([{ content: 'Old plan', status: 'in_progress' }]));
    await act(async () => root.render(<StickyTodoProgress liveTurnStore={store} isActive messages={[]} />));
    expect(host.textContent).toContain('Old plan');
    await act(async () => store.set({
      timelineItems: [...store.getSnapshot().timelineItems, ...turnWithTodos([]).timelineItems],
    }));
    expect(host.querySelector('details')).toBeNull();
  });

  it('does not show the previous completed plan during a new user turn', async () => {
    const messages = [
      { role: 'assistant', ...turnWithTodos([{ content: 'Previous plan', status: 'completed' }]) },
      { role: 'user', content: 'Start something else.' },
    ];
    await act(async () => root.render(
      <StickyTodoProgress liveTurnStore={createLiveAssistantTurnStore()} isActive messages={messages} />,
    ));
    expect(host.querySelector('details')).toBeNull();
  });
  it('hides completed plans once their turn is inactive', async () => {
    const store = createLiveAssistantTurnStore(turnWithTodos([{ content: 'Finished', status: 'completed' }]));
    await act(async () => root.render(<StickyTodoProgress liveTurnStore={store} isActive={false} messages={[]} />));
    expect(host.textContent).toBe('');
  });
});
