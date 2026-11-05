import { describe, expect, it } from 'vitest';
import { UI_EVENT } from '@frontend/realtime/eventVocabulary';
import { eventHandlers, initialState, reducers } from '@frontend/features/chat/state/chats';
import { reconcileChatPagination } from '@frontend/features/chat/state/actions';
import { mergeCanonicalMessages } from '@frontend/features/chat/models/conversationModel';

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

describe('reconciled chat pagination', () => {
  const canonicalMessages = [
    { id: 'canonical-question', role: 'user', content: 'Latest question' },
    { id: 'canonical-answer', role: 'assistant', content: 'Complete answer' },
  ];

  function prepareReconciliation({ hasMoreMessages = false, canonicalHasMore = true } = {}) {
    const state = structuredClone(initialState);
    state.chats.active.threadId = 'thread';
    state.chats.active.hasMoreMessages = hasMoreMessages;
    state.chats.reconciliation.requestId = 'reconcile';
    eventHandlers[UI_EVENT.CHAT_SESSION_SELECTED](state, {
      request_id: 'reconcile',
      session: { thread_id: 'thread', messages: canonicalMessages, has_more_messages: canonicalHasMore },
    });
    return state;
  }

  it('restores older-page availability when a partial canonical page replaces the transcript', () => {
    const state = prepareReconciliation();
    const older = { id: 'older-page', role: 'user', content: 'Earlier page' };
    const merged = mergeCanonicalMessages([older], canonicalMessages, {
      preserveBeforeId: canonicalMessages[0].id,
    });

    reducers.reconcileChatPagination(state, reconcileChatPagination({
      threadId: 'thread',
      requestId: 'reconcile',
      preserveOlderMessages: merged.length > canonicalMessages.length,
    }));

    expect(merged).not.toContain(older);
    expect(state.chats.active.hasMoreMessages).toBe(true);
  });

  it('retains the exhausted paging state when loaded older messages survive', () => {
    const state = prepareReconciliation();
    const older = { id: 'older-page', role: 'user', content: 'Earlier page' };
    const merged = mergeCanonicalMessages([older, ...canonicalMessages], canonicalMessages, {
      preserveBeforeId: canonicalMessages[0].id,
    });

    reducers.reconcileChatPagination(state, reconcileChatPagination({
      threadId: 'thread',
      requestId: 'reconcile',
      preserveOlderMessages: merged.length > canonicalMessages.length,
    }));

    expect(merged[0]).toBe(older);
    expect(state.chats.active.hasMoreMessages).toBe(false);
  });

  it('stops offering older pages when the replacement is complete', () => {
    const state = prepareReconciliation({ hasMoreMessages: true, canonicalHasMore: false });

    reducers.reconcileChatPagination(state, reconcileChatPagination({
      threadId: 'thread',
      requestId: 'reconcile',
      preserveOlderMessages: false,
    }));

    expect(state.chats.active.hasMoreMessages).toBe(false);
  });

  it.each([
    { threadId: 'thread', requestId: 'stale-request', activeThreadId: 'thread' },
    { threadId: 'other-thread', requestId: 'reconcile', activeThreadId: 'thread' },
    { threadId: 'thread', requestId: 'reconcile', activeThreadId: 'new-selection' },
  ])('ignores pagination from an unowned reconciliation: %j', ({ activeThreadId, ...payload }) => {
    const state = prepareReconciliation();
    state.chats.active.threadId = activeThreadId;

    reducers.reconcileChatPagination(state, reconcileChatPagination(payload));

    expect(state.chats.active.hasMoreMessages).toBe(false);
  });
});
