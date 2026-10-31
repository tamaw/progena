import { describe, expect, it } from 'vitest';
import { eventHandlers, initialState } from '@frontend/features/chat/state/chats';
import { UI_EVENT } from '@frontend/realtime/eventVocabulary';

function stateWithActiveThread(threadId = 'thread-1') {
  const state = structuredClone(initialState);
  state.chats.active.threadId = threadId;
  state.chats.active.detail = { thread_id: threadId, messages: [] };
  return state;
}

describe('chat response lifecycle ownership', () => {
  it('keeps the outer content request as owner across a queued response lifecycle', () => {
    const state = stateWithActiveThread();
    eventHandlers[UI_EVENT.CHAT_RESPONSE_STARTED](state, {
      request_id: 'request-a',
      thread_id: 'thread-1',
    });
    eventHandlers[UI_EVENT.CHAT_RESPONSE_STARTED](state, {
      request_id: 'queued-request-b',
      content_request_id: 'request-a',
      thread_id: 'thread-1',
    });

    expect(state.chatRuntime.requestIdByThreadId['thread-1']).toBe('request-a');
    expect(state.chatRuntime.responseThreadsByRequestId).toEqual({ 'request-a': 'thread-1' });

    eventHandlers[UI_EVENT.CHAT_RESPONSE_COMPLETED](state, {
      request_id: 'queued-request-b',
      content_request_id: 'request-a',
      thread_id: 'thread-1',
    });
    expect(state.chatRuntime.requestIdByThreadId).toEqual({});
  });

  it('retains active history and pagination for an unrelated session upsert', () => {
    const state = stateWithActiveThread();
    state.chats.active.detail = {
      thread_id: 'thread-1',
      messages: [{ id: 'older-message', role: 'assistant', content: 'Older loaded page' }],
    };
    state.chats.active.pendingOlderPage = {
      requestId: 'older-page-request',
      messages: [],
      summaryMarkers: [],
    };
    eventHandlers[UI_EVENT.CHAT_SESSION_UPSERTED](state, {
      session: {
        thread_id: 'thread-1',
        messages: [{ id: 'newer-message', role: 'assistant', content: 'Unrelated list update' }],
      },
    });

    expect(state.chats.active.detail.messages).toEqual([
      expect.objectContaining({ id: 'older-message' }),
    ]);
    expect(state.chats.active.pendingOlderPage).toEqual(
      expect.objectContaining({ requestId: 'older-page-request' }),
    );
  });
});
