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
        { id: 'first', text: 'First pending', attachments: [], status: 'queued', send_now: true, created_at: '2026-11-04T08:15:30Z' },
        { id: 'sending', text: 'Claimed already', attachments: [], status: 'sending', created_at: '2026-11-04T08:16:00Z' },
        { id: 'third', text: 'Third pending', attachments: [], status: 'queued', created_at: '2026-11-04T08:17:00Z' },
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
  });


  it('keeps queued content open when its send and cancel actions are used', async () => {
    await act(async () => {
      host.querySelector('[aria-label="Send queued message now"]').click();
      host.querySelector('[aria-label="Cancel queued message"]').click();
    });
    expect(host.querySelector('details').open).toBe(true);
  });
  it('renders queued content in an initially open native disclosure', async () => {
    const disclosure = host.querySelector('details');
    expect(disclosure.open).toBe(true);
    await act(async () => {
      disclosure.querySelector('summary').click();
    });
    expect(disclosure.open).toBe(false);
  });

  it('does not offer mutable controls for a runtime-claimed message', () => {
    const claimed = [...host.querySelectorAll('article')].find((item) => item.textContent.includes('Claimed already'));
    expect(claimed.querySelector('[aria-label="Cancel queued message"]')).toBeNull();
    expect(claimed.textContent).toContain('Sending');
  });
});
