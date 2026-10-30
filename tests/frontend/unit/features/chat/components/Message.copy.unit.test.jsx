// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Message } from '@frontend/features/chat/components/Message';
import ChatMessages from '@frontend/features/chat/components/ChatMessages';

vi.mock('react-redux', () => ({
  useSelector: (selector) => selector({ uiData: { preferences: { agentName: 'Progena' } } }),
}));

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

function assistantMessage(id, content) {
  return {
    id,
    role: 'assistant',
    content,
    responseStatus: 'completed',
    timelineItems: [{
      kind: 'assistant_message',
      id: `${id}-answer`,
      content,
      timestamp: 1_000,
      isStreaming: false,
      error: false,
    }],
  };
}

function action(name) {
  const button = host.querySelector(`button[aria-label="${name}"]`);
  expect(button).not.toBeNull();
  return button;
}

describe('assistant message actions', () => {
  it('copies the timeline answer content and exposes copy feedback', async () => {
    const clipboard = { writeText: vi.fn(() => Promise.resolve()) };
    vi.stubGlobal('navigator', { clipboard });
    const response = 'Copy this answer exactly, including punctuation.';

    await act(async () => root.render(<Message message={assistantMessage('assistant-copy', response)} />));
    const copy = action('Copy message');
    await act(async () => copy.click());

    expect(clipboard.writeText).toHaveBeenCalledWith(response);
    expect(action('Copy message').dataset.tooltip).toBe('Copied');
  });

  it('regenerates the selected historical assistant message and exposes feedback', async () => {
    const onRegenerate = vi.fn();
    const historicalAssistant = assistantMessage('assistant-regenerate', 'Regenerate this response.');

    await act(async () => root.render(
      <ChatMessages
        messages={[
          { id: 'user-before', role: 'user', content: 'Original question' },
          historicalAssistant,
        ]}
        onRegenerate={onRegenerate}
      />,
    ));
    const regenerate = action('Regenerate response');
    await act(async () => regenerate.click());

    expect(onRegenerate).toHaveBeenCalledTimes(1);
    expect(onRegenerate).toHaveBeenCalledWith(historicalAssistant.id);
    expect(action('Regenerate response').dataset.tooltip).toBe('Regenerated');
  });
});
