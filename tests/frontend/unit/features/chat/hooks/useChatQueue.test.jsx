// @vitest-environment jsdom
import React, { act, createRef, forwardRef, useImperativeHandle } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createRoot } from 'react-dom/client';

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const mocks = vi.hoisted(() => ({
  listener: null,
  requestAcknowledged: vi.fn(),
  nextId: 0,
  getApiUrl: (path) => path,
}));

vi.mock('@frontend/context/ConfigContext', () => ({
  useConfig: () => ({ getApiUrl: mocks.getApiUrl }),
}));
vi.mock('@frontend/realtime/realtimeEvents', () => ({
  subscribeUiEvent: (listener) => {
    mocks.listener = listener;
    return () => { mocks.listener = null; };
  },
}));
vi.mock('@frontend/store/http', () => ({
  requestAcknowledged: mocks.requestAcknowledged,
}));
vi.mock('@frontend/lib/uuid', () => ({
  createUuid: () => `00000000-0000-4000-8000-${String(++mocks.nextId).padStart(12, '0')}`,
}));

import { useChatQueue } from '@frontend/features/chat/hooks/useChatQueue';
import { UI_EVENT } from '@frontend/realtime/eventVocabulary';

let host;
let root;
let queueRef;

const Harness = forwardRef(function Harness(_props, ref) {
  const queue = useChatQueue('thread-1');
  useImperativeHandle(ref, () => queue, [queue]);
  return null;
});

async function publish(payload) {
  await act(async () => {
    mocks.listener({ type: UI_EVENT.CHAT_QUEUE_UPDATED, payload });
    await Promise.resolve();
  });
}

beforeEach(async () => {
  mocks.listener = null;
  mocks.nextId = 0;
  mocks.requestAcknowledged.mockReset();
  mocks.requestAcknowledged.mockResolvedValue({ request_id: 'ack', event_types: [UI_EVENT.CHAT_QUEUE_UPDATED] });
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
  queueRef = createRef();
  await act(async () => {
    root.render(<Harness ref={queueRef} />);
    await Promise.resolve();
  });
});

afterEach(async () => {
  await act(async () => root?.unmount());
  host?.remove();
});

describe('useChatQueue', () => {
  it('hydrates only the active conversation from the canonical snapshot event', async () => {
    expect(mocks.requestAcknowledged).toHaveBeenCalledWith(
      '/api/chat/sessions/thread-1/queue',
      expect.objectContaining({ requestId: expect.any(String) }),
    );

    await publish({ conversation_id: 'other', revision: 1, messages: [] });
    expect(queueRef.current.snapshot).toBeNull();

    await publish({
      conversation_id: 'thread-1',
      revision: 3,
      messages: [
        { id: 'queued-1', text: 'Wait for this', attachments: [], status: 'queued' },
        { id: 'sending-1', text: 'Already claimed', attachments: [], status: 'sending' },
      ],
    });
    expect(queueRef.current.snapshot).toEqual({
      conversationId: 'thread-1',
      revision: 3,
      messages: expect.arrayContaining([
        expect.objectContaining({ id: 'queued-1', status: 'queued' }),
        expect.objectContaining({ id: 'sending-1', status: 'sending' }),
      ]),
    });
  });

  it('ignores stale queue snapshots and rehydrates after the canonical stream reconnects', async () => {
    await publish({
      conversation_id: 'thread-1',
      revision: 5,
      messages: [{ id: 'newer', text: 'Current state', attachments: [], status: 'queued' }],
    });
    await publish({
      conversation_id: 'thread-1',
      revision: 4,
      messages: [{ id: 'stale', text: 'Stale state', attachments: [], status: 'queued' }],
    });

    expect(queueRef.current.snapshot).toEqual(expect.objectContaining({
      revision: 5,
      messages: [expect.objectContaining({ id: 'newer' })],
    }));

    const requestsBeforeReconnect = mocks.requestAcknowledged.mock.calls.length;
    await act(async () => {
      mocks.listener({ type: UI_EVENT.CONNECTION_READY });
      await Promise.resolve();
    });
    expect(mocks.requestAcknowledged).toHaveBeenCalledTimes(requestsBeforeReconnect + 1);
    expect(mocks.requestAcknowledged).toHaveBeenLastCalledWith(
      '/api/chat/sessions/thread-1/queue',
      expect.objectContaining({ requestId: expect.any(String) }),
    );
  });

  it('releases a pending command fence when reconnect recovery starts', async () => {
    await publish({
      conversation_id: 'thread-1',
      revision: 1,
      messages: [{ id: 'queued-1', text: 'Wait', attachments: [], status: 'queued' }],
    });
    await act(async () => {
      await queueRef.current.deleteMessage('queued-1');
    });
    expect(queueRef.current.isMutating).toBe(true);

    await act(async () => {
      mocks.listener({ type: UI_EVENT.CONNECTION_READY });
      await Promise.resolve();
    });
    expect(queueRef.current.isMutating).toBe(false);
  });

  it('releases an idempotent command fence when its returned snapshot has the current revision', async () => {
    await publish({
      conversation_id: 'thread-1',
      revision: 2,
      messages: [{ id: 'queued-1', text: 'Wait', attachments: [], status: 'queued' }],
    });
    await act(async () => {
      await queueRef.current.sendNow('queued-1');
    });
    const commandId = mocks.requestAcknowledged.mock.calls.at(-1)[1].body.command_id;
    expect(queueRef.current.isMutating).toBe(true);

    await publish({
      conversation_id: 'thread-1',
      revision: 2,
      request_id: commandId,
      messages: [{ id: 'queued-1', text: 'Wait', attachments: [], status: 'queued' }],
    });
    expect(queueRef.current.isMutating).toBe(false);
  });

  it('sends the full authoritative revision and command id for a reorder', async () => {
    await publish({
      conversation_id: 'thread-1',
      revision: 7,
      messages: [{ id: 'queued-1', text: 'Wait', attachments: [], status: 'queued' }],
    });

    await act(async () => {
      await queueRef.current.reorderMessages(['queued-1']);
    });

    expect(mocks.requestAcknowledged).toHaveBeenLastCalledWith(
      '/api/chat/sessions/thread-1/queue/actions',
      expect.objectContaining({
        method: 'POST',
        body: expect.objectContaining({
          action: 'reorder',
          order: ['queued-1'],
          revision: 7,
          command_id: expect.any(String),
        }),
      }),
    );
  });

  it('takes a one-shot authoritative snapshot after a rejected queue action', async () => {
    await publish({
      conversation_id: 'thread-1',
      revision: 8,
      messages: [{ id: 'queued-1', text: 'Wait', attachments: [], status: 'queued' }],
    });
    mocks.requestAcknowledged.mockRejectedValueOnce(new Error('Queue revision is stale'));

    await act(async () => {
      await queueRef.current.deleteMessage('queued-1');
    });

    expect(mocks.requestAcknowledged).toHaveBeenLastCalledWith(
      '/api/chat/sessions/thread-1/queue',
      expect.objectContaining({ requestId: expect.any(String) }),
    );
  });
});
