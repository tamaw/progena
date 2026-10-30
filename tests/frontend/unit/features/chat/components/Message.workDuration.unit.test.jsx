// @vitest-environment jsdom
import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import Message from '@frontend/features/chat/components/Message';

vi.mock('react-redux', () => ({
  useSelector: (selector) => selector({ uiData: { preferences: { agentName: 'Progena' } } }),
}));

function renderTurn(message) {
  const container = document.createElement('div');
  container.innerHTML = renderToStaticMarkup(<Message message={message} />);
  return container;
}

describe('Message work duration', () => {
  it('measures root work through the first nonempty answer after tool commentary', () => {
    const tool = (id, eventType, timestamp) => ({
      kind: 'stream_event',
      id,
      timestamp,
      event: {
        id,
        eventType,
        timestamp,
        content: '',
        metadata: { tool_name: 'read_file', tool_call_id: 'read', args_preview: { path: '/config' } },
      },
    });
    const message = {
      id: 'turn',
      role: 'assistant',
      responseStatus: 'completed',
      timelineItems: [
        tool('start', 'tool_start', 1_000),
        tool('result', 'tool_result', 1_200),
        // The model's earlier narration was claimed as tool commentary.
        { kind: 'assistant_message', id: 'commentary', content: '', timestamp: 1_300 },
        { kind: 'assistant_message', id: 'answer', content: 'Finished answer', timestamp: 18_000 },
      ],
    };

    const visible = renderTurn(message);
    const summary = visible.querySelector('.work-group__toggle');

    expect(summary?.textContent).toContain('Worked for 17s');
    expect(visible.textContent).toContain('Finished answer');
  });

  it('stops the latest work group when answer text has started even while the turn remains live', () => {
    const message = {
      id: 'turn',
      role: 'assistant',
      workActive: true,
      responseStatus: 'running',
      timelineItems: [
        {
          kind: 'stream_event',
          id: 'tool',
          timestamp: 1_000,
          event: {
            id: 'tool',
            eventType: 'tool_start',
            timestamp: 1_000,
            content: '',
            metadata: { tool_name: 'read_file', tool_call_id: 'read' },
          },
        },
        {
          kind: 'assistant_message',
          id: 'answer',
          timestamp: 2_000,
          content: 'Streaming answer',
          isStreaming: true,
        },
      ],
    };

    const visible = renderTurn(message);
    const summary = visible.querySelector('.work-group__toggle');

    expect(visible.querySelector('.work-group--active')).toBeNull();
    expect(summary?.getAttribute('aria-expanded')).toBe('false');
    expect(summary?.textContent).not.toContain('Unknown');
  });
});
