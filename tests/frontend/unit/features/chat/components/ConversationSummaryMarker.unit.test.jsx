// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import ConversationSummaryMarker from '@frontend/features/chat/components/ConversationSummaryMarker';

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

describe('ConversationSummaryMarker', () => {
  it('keeps the actual summary lazy until its transcript line is expanded', async () => {
    await act(async () => root.render(
      <ConversationSummaryMarker marker={{
        id: 'summary-42',
        anchorMessageId: 'before',
        summary: '## Retained history\n\nThe user chose **green**.',
        scope: { kind: 'conversation', id: 'thread-1' },
      }} />,
    ));

    const marker = host.querySelector('#conversation-summary-summary-42');
    const details = marker.querySelector('details');
    expect(marker.dataset.summaryScope).toBe('thread-1');
    const summary = details.querySelector('summary');
    expect(summary.textContent).toContain('Conversation summarized');
    expect(marker.querySelector('.message-content')).toBeNull();
    expect(details.open).toBe(false);

    await act(async () => {
      const toggled = new Promise((resolve) => details.addEventListener('toggle', resolve, { once: true }));
      summary.click();
      await toggled;
    });

    const body = marker.querySelector('.message-content');
    expect(details.open).toBe(true);
    expect(body.textContent).toContain('Retained history');
    expect(body.textContent).toContain('The user chose green.');
  });
});
