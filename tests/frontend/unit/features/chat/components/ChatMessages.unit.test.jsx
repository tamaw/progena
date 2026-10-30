// @vitest-environment jsdom
import React, { act } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { createRoot } from 'react-dom/client';
import { renderToStaticMarkup } from 'react-dom/server';
import { Provider } from 'react-redux';
import { configureStore } from '@reduxjs/toolkit';
import ChatMessages from '@frontend/features/chat/components/ChatMessages';
import { isEventVisible } from '@frontend/features/chat/models/streamFilters';
import { mergeConversationSummaryMarkers } from '@frontend/features/chat/models/conversationSummaryModel';
import { createLiveAssistantTurnStore } from '@frontend/features/chat/models/liveAssistantTurnStore';
import { createLiveAssistantTurn } from '@frontend/features/chat/models/liveAssistantTurnModel';
import { useMermaidRender } from '@frontend/hooks/useMermaidRender';

vi.mock('@frontend/hooks/useMermaidRender', () => ({
  useMermaidRender: vi.fn(),
}));

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

describe('conversation summary markers', () => {
  it('renders a committed marker at its anchored boundary without replacing either message', () => {
    const store = configureStore({
      reducer: () => ({ uiData: { preferences: { agentName: 'Progena' } } }),
    });
    const container = document.createElement('div');
    container.innerHTML = renderToStaticMarkup(
      <Provider store={store}>
        <ChatMessages
          messages={[
            { id: 'before', role: 'user', content: 'Before summary' },
            { id: 'after', role: 'assistant', content: 'After summary' },
          ]}
          summaryMarkers={[{
            id: 'summary-1',
            anchor_message_id: 'before',
            summary: '## Condensed context\n\nEarlier details.',
            scope: { kind: 'conversation', id: 'thread-1' },
          }]}
          streamEventFilter={() => false}
        />
      </Provider>,
    );

    const marker = container.querySelector('[data-summary-scope]');
    expect(marker).not.toBeNull();
    expect(container.querySelector('.msg--user').compareDocumentPosition(marker) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(marker.compareDocumentPosition(container.querySelector('.msg--assistant')) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('keeps live compaction before the pending answer that terminates the response chain', () => {
    const store = configureStore({
      reducer: () => ({ uiData: { preferences: { agentName: 'Progena' } } }),
    });
    const liveStore = createLiveAssistantTurnStore({
      ...createLiveAssistantTurn({ streamId: 'pending-compaction', startedAt: 1 }),
      isStreaming: true,
      summaryActivities: [{
        id: 'compacting',
        scope: { kind: 'conversation', id: 'thread-1' },
      }],
    });
    const container = document.createElement('div');
    container.innerHTML = renderToStaticMarkup(
      <Provider store={store}>
        <ChatMessages
          messages={[{ id: 'question', role: 'user', content: 'Continue the work.' }]}
          liveTurnStore={liveStore}
        />
      </Provider>,
    );
    const activity = container.querySelector('article[role="status"]');
    const answer = container.querySelector('[aria-label="Assistant is responding"]');
    expect(activity).not.toBeNull();
    expect(answer).not.toBeNull();
    expect(activity.compareDocumentPosition(answer) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('keeps a committed marker visible after the frozen live owner', () => {
    const store = configureStore({
      reducer: () => ({ uiData: { preferences: { agentName: 'Progena' } } }),
    });
    const liveStore = createLiveAssistantTurnStore();
    liveStore.set({
      ...createLiveAssistantTurn({ streamId: 'live-summary', startedAt: 1 }),
      assistantMessageId: 'live-answer',
      content: 'Frozen answer',
      isStreaming: false,
      timelineItems: [{
        kind: 'assistant_message',
        id: 'live-answer',
        content: 'Frozen answer',
        timestamp: 1,
        isStreaming: false,
      }],
    });
    const container = document.createElement('div');
    container.innerHTML = renderToStaticMarkup(
      <Provider store={store}>
        <ChatMessages
          messages={[]}
          summaryMarkers={[{
            id: 'summary-after-live',
            anchor_message_id: 'live-answer',
            summary: 'Committed after the live answer.',
            scope: { kind: 'conversation', id: 'thread-1' },
          }]}
          liveOwnedMessageId="live-answer"
          liveTurnStore={liveStore}
        />
      </Provider>,
    );

    expect(container.querySelector('.msg--assistant').compareDocumentPosition(container.querySelector('[data-summary-scope]')) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
});

describe('conversation summary marker render stability', () => {
  it('does not rerender unrelated historical messages for equal or differently scoped markers', async () => {
    const store = configureStore({
      reducer: () => ({ uiData: { preferences: { agentName: 'Progena' } } }),
    });
    const messages = [
      {
        id: 'assistant-a',
        role: 'assistant',
        content: 'First answer',
        timelineItems: [{
          kind: 'subagent',
          id: 'subagent-a',
          toolCallId: 'tool-a',
          status: 'success',
          subagentName: 'Delegate A',
          entries: [],
        }],
      },
      {
        id: 'assistant-b',
        role: 'assistant',
        content: 'Second answer',
        timelineItems: [{
          kind: 'subagent',
          id: 'subagent-b',
          toolCallId: 'tool-b',
          status: 'success',
          subagentName: 'Delegate B',
          entries: [],
        }],
      },
    ];
    const markerA = {
      id: 'summary-a',
      anchor_message_id: 'assistant-a',
      summary: 'First nested summary.',
      scope: { kind: 'subagent', id: 'tool-a' },
    };
    const markerB = {
      id: 'summary-b',
      anchor_message_id: 'assistant-b',
      summary: 'Second nested summary.',
      scope: { kind: 'subagent', id: 'tool-b' },
    };
    const initialMarkers = mergeConversationSummaryMarkers([], [markerA]);
    const equalMarkers = mergeConversationSummaryMarkers(initialMarkers, [{ ...markerA }]);
    const changedMarkers = mergeConversationSummaryMarkers(equalMarkers, [markerB]);
    const host = document.createElement('div');
    document.body.append(host);
    const root = createRoot(host);

    try {
      await act(async () => {
        root.render(
          <Provider store={store}>
            <ChatMessages messages={messages} summaryMarkers={initialMarkers} />
          </Provider>,
        );
      });
      const cards = host.querySelectorAll('.subagent-card');
      expect(cards).toHaveLength(2);
      expect(cards[0].querySelector('[data-summary-scope]')).not.toBeNull();
      expect(cards[1].querySelector('[data-summary-scope]')).toBeNull();
      useMermaidRender.mockClear();

      await act(async () => {
        root.render(
          <Provider store={store}>
            <ChatMessages messages={messages} summaryMarkers={equalMarkers} />
          </Provider>,
        );
      });
      expect(useMermaidRender).not.toHaveBeenCalled();

      await act(async () => {
        root.render(
          <Provider store={store}>
            <ChatMessages messages={messages} summaryMarkers={changedMarkers} />
          </Provider>,
        );
      });
      expect(useMermaidRender).toHaveBeenCalledTimes(1);
    } finally {
      await act(async () => root.unmount());
      host.remove();
    }
  });
});
