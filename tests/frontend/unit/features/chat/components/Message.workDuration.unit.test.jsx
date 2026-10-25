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
  container.innerHTML = renderToStaticMarkup(React.createElement(Message, { message }));
  return container;
}

describe('Message work duration', () => {
  it('uses the first nonempty answer boundary after narration becomes tool commentary', () => {
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
      timelineItems: [
        tool('start', 'tool_start', 1000),
        tool('result', 'tool_result', 1200),
        // The model's earlier narration was claimed as tool commentary.
        { kind: 'assistant_message', id: 'commentary', content: '', timestamp: 1300 },
        { kind: 'assistant_message', id: 'answer', content: 'Finished answer', timestamp: 18000 },
      ],
    };

    const visible = renderTurn(message);
    expect(visible.querySelector('.work-group__title').textContent).toContain('17s');
    expect(visible.querySelector('.msg--assistant').textContent).toContain('Finished answer');
  });
});
