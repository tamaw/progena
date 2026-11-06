import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { UI_EVENT } from '@frontend/realtime/eventVocabulary';
import { applyChatRowDelta, deleteChatFromList, eventHandlers, initialState, reducers } from '@frontend/features/chat/state/chats';
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

describe('persisted chat indicators', () => {
  it('hydrates flags without a local generation and retains errors while a retry starts', () => {
    const state = structuredClone(initialState);
    eventHandlers[UI_EVENT.CHAT_LIST_ROW_UPSERTED](state, {
      row: { thread_id: 'external', unread: true, errored: true },
    });
    expect(state.chatRuntime.unreadChatIds).toEqual(['external']);
    expect(state.chatRuntime.erroredChatIds).toEqual(['external']);
    state.chats.active.threadId = 'external';
    eventHandlers[UI_EVENT.CHAT_RESPONSE_STARTED](state, { thread_id: 'external', request_id: 'retry' });
    expect(state.chatRuntime.erroredChatIds).toEqual(['external']);
    eventHandlers[UI_EVENT.CHAT_RESPONSE_FAILED](state, { thread_id: 'external', request_id: 'retry' });
    expect(state.chatRuntime.erroredChatIds).toEqual(['external']);
    eventHandlers[UI_EVENT.CHAT_LIST_ROW_UPSERTED](state, {
      row: { thread_id: 'external', unread: false, errored: true },
    });
    expect(state.chatRuntime.unreadChatIds).toEqual([]);
    expect(state.chatRuntime.erroredChatIds).toEqual(['external']);
    eventHandlers[UI_EVENT.CHAT_RESPONSE_STARTED](state, { thread_id: 'external', request_id: 'success' });
    eventHandlers[UI_EVENT.CHAT_RESPONSE_COMPLETED](state, { thread_id: 'external', request_id: 'success' });
    expect(state.chatRuntime.erroredChatIds).toEqual([]);
  });
});

describe('chat list row updates', () => {
  it('keeps an existing row when only a server-only first-message search match is unknown locally', () => {
    const state = structuredClone(initialState);
    state.chats.view = { q: 'server-only phrase', sort: 'recent', filter: 'active' };
    state.chats.byId.chat = {
      thread_id: 'chat',
      title: 'Unrelated title',
      preview: 'Unrelated preview',
      archived: false,
    };
    state.chats.rowsByIndex = { 0: 'chat' };
    state.chats.total = 1;

    eventHandlers[UI_EVENT.CHAT_LIST_ROW_UPSERTED](state, {
      row: {
        thread_id: 'chat',
        title: 'Still unrelated locally',
        preview: 'Still unrelated locally',
        archived: false,
      },
    });

    expect(state.chats.rowsByIndex).toEqual({ 0: 'chat' });
    expect(state.chats.total).toBe(1);
  });
});

describe('positional chat cache deltas', () => {
  // Row fixtures are dated 2026-11-05 and "today" is read from the clock.
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-11-05T12:30:00Z'));
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it('does not inflate totals when an unloaded conversation changes', () => {
    const state = structuredClone(initialState);
    state.chats.total = 100;
    state.chats.rowsByIndex = { 0: 'first' };
    state.chats.byId.first = { thread_id: 'first', title: 'First' };
    state.chats.loadedPages = { 0: true };
    eventHandlers[UI_EVENT.CHAT_LIST_ROW_UPSERTED](state, {
      row: { thread_id: 'unloaded', title: 'Updated', unread: true },
    });
    expect(state.chats.total).toBe(100);
    expect(state.chats.rowsByIndex).toEqual({ 0: 'first' });
    expect(state.chats.loadedPages).toEqual({});
  });

  function cache() {
    const state = structuredClone(initialState).chats;
    state.total = 3;
    state.groups = [
      { key: 'pinned', count: 1 },
      { key: 'today', count: 2 },
      { key: 'yesterday', count: 0 },
      { key: 'this-week', count: 0 },
      { key: 'last-week', count: 0 },
      { key: 'later', count: 0 },
    ];
    state.rowsByIndex = { 0: 'pinned', 1: 'first', 2: 'second' };
    state.byId = {
      pinned: { thread_id: 'pinned', pinned: true, archived: false, last_activity: '2026-11-05T11:00:00Z' },
      first: { thread_id: 'first', pinned: false, archived: false, last_activity: '2026-11-05T10:00:00Z' },
      second: { thread_id: 'second', pinned: false, archived: false, last_activity: '2026-11-05T09:00:00Z' },
    };
    return state;
  }

  it('moves a refreshed row to the top of its section and shifts cached indexes', () => {
    const chats = cache();
    applyChatRowDelta(chats, {
      ...chats.byId.second,
      last_activity: '2026-11-05T12:00:00Z',
    });

    expect(chats.rowsByIndex).toEqual({ 0: 'pinned', 1: 'second', 2: 'first' });
    expect(chats.total).toBe(3);
  });

  it('inserts a recent row at its section boundary and deletes a loaded row without resurrecting it', () => {
    const chats = cache();
    applyChatRowDelta(chats, {
      thread_id: 'new',
      pinned: false,
      archived: false,
      last_activity: '2026-11-05T12:00:00Z',
    });
    expect(chats.rowsByIndex).toEqual({ 0: 'pinned', 1: 'new', 2: 'first', 3: 'second' });
    expect(chats.groups.find((group) => group.key === 'today').count).toBe(3);

    deleteChatFromList(chats, 'first');
    expect(chats.rowsByIndex).toEqual({ 0: 'pinned', 1: 'new', 2: 'second' });
    expect(chats.total).toBe(3);
    expect(chats.tombstones.first).toBe(true);
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
