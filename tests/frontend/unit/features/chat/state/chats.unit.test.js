import { describe, expect, it } from 'vitest';
import { UI_EVENT } from '@frontend/realtime/eventVocabulary';
import { eventHandlers, initialState } from '@frontend/features/chat/state/chats';

describe('queue lifecycle ownership', () => {
  it('does not let queued acceptance or late A completion replace B', () => {
    const state = structuredClone(initialState);
    eventHandlers[UI_EVENT.CHAT_RESPONSE_STARTED](state, { thread_id: 'thread', request_id: 'A' });
    eventHandlers[UI_EVENT.CHAT_RESPONSE_STARTED](state, { thread_id: 'thread', request_id: 'B-request', content_request_id: 'A', queued: true });
    expect(state.chatRuntime.requestIdByThreadId.thread).toBe('A');
    eventHandlers[UI_EVENT.CHAT_RESPONSE_STARTED](state, { thread_id: 'thread', request_id: 'B-request', content_request_id: 'B', queue_execution: true });
    eventHandlers[UI_EVENT.CHAT_RESPONSE_COMPLETED](state, { thread_id: 'thread', request_id: 'A' });
    expect(state.chatRuntime.requestIdByThreadId.thread).toBe('B');
  });
});
