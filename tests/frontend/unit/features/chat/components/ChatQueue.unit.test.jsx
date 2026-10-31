// @vitest-environment jsdom
import React, { act } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createRoot } from 'react-dom/client';
import ChatQueue from '@frontend/features/chat/components/ChatQueue';

let host;
let root;
let props;

beforeEach(async () => {
  props = {
    snapshot: {
      revision: 4,
      messages: [
        { id: 'first', text: 'First pending', attachments: [], status: 'queued', send_now: true },
        { id: 'sending', text: 'Claimed already', attachments: [], status: 'sending' },
        { id: 'third', text: 'Third pending', attachments: [], status: 'queued' },
      ],
    },
    isMutating: false,
    onDelete: vi.fn(),
    onReorder: vi.fn(),
    onSendNow: vi.fn(),
  };
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
  await act(async () => root.render(<ChatQueue {...props} />));
});

afterEach(async () => {
  await act(async () => root?.unmount());
  host?.remove();
});

describe('ChatQueue', () => {
  it('uses accessible movement controls and submits all pending ids for reorder', async () => {
    await act(async () => {
      host.querySelector('[aria-label="Move queued message later"]').click();
    });
    expect(props.onReorder).toHaveBeenCalledWith(['third', 'first']);
    expect(host.textContent).toContain('Sending next');
  });

  it('does not offer mutable controls for a runtime-claimed message', () => {
    const claimed = [...host.querySelectorAll('li')].find((item) => item.textContent.includes('Claimed already'));
    expect(claimed.querySelector('[aria-label="Delete queued message"]')).toBeNull();
    expect(claimed.textContent).toContain('Sending…');
  });
});
