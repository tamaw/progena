// @vitest-environment jsdom
import React from 'react';
import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { Provider } from 'react-redux';
import { configureStore } from '@reduxjs/toolkit';
import ChatMessages from '@frontend/features/chat/components/ChatMessages';
import { isEventVisible } from '@frontend/features/chat/models/streamFilters';
import { createLiveAssistantTurnStore } from '@frontend/features/chat/models/liveAssistantTurnStore';
import { createLiveAssistantTurn } from '@frontend/features/chat/models/liveAssistantTurnModel';

function renderTranscript(surface, filters) {
  const store = configureStore({
    reducer: () => ({ uiData: { preferences: { agentName: 'Progena' } } }),
  });
  const event = (eventType, timestamp) => ({
    id: eventType, kind: 'stream_event', timestamp,
    event: {
      id: eventType, eventType, timestamp, content: '',
      metadata: { tool_name: 'read_file', tool_call_id: 'read', error: eventType === 'tool_error' ? 'File missing' : undefined },
    },
  });
  const assistant = {
    id: 'assistant', role: 'assistant', content: 'The answer',
    streamEvents: [{
      id: 'image', eventType: 'tool_result',
      metadata: { tool_name: 'generate_image' },
      images: [{ id: 'image', base64: 'AAA', media_type: 'image/png' }],
    }],
    timelineItems: [
      event('tool_start', 1000), event('tool_error', 2000),
      { kind: 'subagent', id: 'successful', status: 'success', subagentName: 'Successful delegate', entries: [] },
      { kind: 'subagent', id: 'failed', status: 'error', subagentName: 'Failed delegate', error: 'Delegate failed', entries: [] },
      { kind: 'a2a_call', id: 'failed-peer', status: 'error', agentName: 'Failed peer', error: 'Peer unavailable', messages: [] },
      { kind: 'subagent', id: 'recovered', status: 'success', subagentName: 'Recovered delegate', entries: [{ id: 'nested', kind: 'tool', toolName: 'read_file', status: 'error', error: 'File missing' }] },
      { kind: 'a2a_call', id: 'recovered-peer', status: 'success', agentName: 'Recovered peer', messages: [{ id: 'update', state: 'failed', text: 'Remote task failed' }] },
      { kind: 'assistant_message', id: 'answer', content: 'The answer', timestamp: 3000 },
    ],
  };
  const user = { id: 'user', role: 'user', content: 'The question', attachments: [{ id: 'file', name: 'notes.txt', mime_type: 'text/plain' }] };
  const liveStore = createLiveAssistantTurnStore();
  liveStore.set({ ...createLiveAssistantTurn({ streamId: 'live', startedAt: 1000 }), ...assistant, assistantMessageId: 'assistant' });
  const container = document.createElement('div');
  container.innerHTML = renderToStaticMarkup(
    <Provider store={store}>
      <ChatMessages
        messages={surface === 'history' ? [user, assistant] : [user]}
        liveTurnStore={surface === 'live' ? liveStore : undefined}
        streamEventFilter={(event) => isEventVisible(event, filters)}
      />
    </Provider>,
  );
  return container;
}

describe.each(['history', 'live'])('%s transcript activity filters', (surface) => {
  it('retains messages, attachments, images and failed work while hiding successful activity', () => {
    const container = renderTranscript(surface, { tools: false, plan: false, reasoning: false, delegations: false });
    expect(container.textContent).toContain('The question');
    expect(container.textContent).toContain('The answer');
    expect(container.textContent).toContain('notes.txt');
    expect(container.querySelector('.msg-img-strip img')).not.toBeNull();
    expect(container.textContent).not.toContain('Successful delegate');
    expect(container.textContent).toContain('1 failed');
    expect(container.textContent).toContain('Delegate failed');
    expect(container.textContent).toContain('Peer unavailable');
    expect(container.querySelector('.subagent-card--success .entry__head').textContent).toContain('Recovered delegate1 failed');
    expect(container.querySelector('.a2a-call-card--success .entry__head').textContent).toContain('Recovered peerFailed');
  });

  it('hides failed activity when Errors is off without hiding the answer', () => {
    const container = renderTranscript(surface, { tools: true, delegations: true, errors: false });
    expect(container.querySelector('.work-group')).toBeNull();
    expect(container.textContent).toContain('Successful delegate');
    expect(container.textContent).not.toContain('Failed delegate');
    expect(container.textContent).not.toContain('Failed peer');
    expect(container.textContent).not.toContain('Recovered delegate');
    expect(container.textContent).not.toContain('Recovered peer');
    expect(container.textContent).toContain('The question');
    expect(container.textContent).toContain('The answer');
    expect(container.querySelector('.msg-img-strip img')).not.toBeNull();
  });
});

describe('interrupted chat status', () => {
  it('renders only for a non-streaming interrupted detail', () => {
    const store = configureStore({
      reducer: () => ({ uiData: { preferences: { agentName: 'Progena' } } }),
    });
    const render = (isGenerating) => {
      const container = document.createElement('div');
      container.innerHTML = renderToStaticMarkup(
        <Provider store={store}>
          <ChatMessages
            messages={[]}
            interruption={{ next: ['model'], at: '2026-10-28T12:00:00Z' }}
            isGenerating={isGenerating}
          />
        </Provider>,
      );
      return container.textContent;
    };

    expect(render(false)).toContain('Interrupted — the runtime restarted during this turn.');
    expect(render(true)).not.toContain('Interrupted — the runtime restarted during this turn.');
  });
});
