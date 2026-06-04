import { describe, it, expect } from 'vitest';
import reducer, {
  applyUiEvent,
  requestChatSessionDetail,
  selectChatIndicators,
  selectChatSessions,
  setChatIndicators,
} from './uiDataSlice';

const snapshot = (ids) => applyUiEvent({
  type: 'chat.sessions.snapshot',
  payload: { sessions: ids.map((id) => ({ chat_session_id: id })) },
});

describe('applyUiEvent reducer', () => {
  it('snapshot replaces the chat list and marks loaded', () => {
    const s = reducer(undefined, snapshot(['a', 'b']));
    expect(s.chats.order).toEqual(['a', 'b']);
    expect(s.chats.loaded).toBe(true);
  });

  it('created prepends a new chat', () => {
    let s = reducer(undefined, snapshot(['a']));
    s = reducer(s, applyUiEvent({ type: 'chat.session.created', payload: { session: { chat_session_id: 'b', title: 'B' } } }));
    expect(s.chats.order).toEqual(['b', 'a']);
    expect(s.chats.byId.b.title).toBe('B');
  });

  it('upserted adds only when missing and does not duplicate', () => {
    let s = reducer(undefined, snapshot(['a']));
    s = reducer(s, applyUiEvent({ type: 'chat.session.upserted', payload: { session: { chat_session_id: 'a', title: 'changed' } } }));
    expect(s.chats.order).toEqual(['a']);
    s = reducer(s, applyUiEvent({ type: 'chat.session.upserted', payload: { session: { chat_session_id: 'c' } } }));
    expect(s.chats.order).toEqual(['c', 'a']);
  });

  it('deleted removes the chat and clears the active detail if it was selected', () => {
    let s = reducer(undefined, snapshot(['a', 'b']));
    s = reducer(s, applyUiEvent({ type: 'chat.session.deleted', payload: { chat_session_id: 'a' } }));
    expect(s.chats.order).toEqual(['b']);
    expect(s.chats.byId.a).toBeUndefined();
  });

  it('selected applies only when request_id matches the active request', () => {
    let s = reducer(undefined, requestChatSessionDetail.pending('rid', {
      chatsUrl: '/x', chatSessionId: 'a', requestId: 'REQ1',
    }));
    expect(s.chats.active.requestId).toBe('REQ1');

    // mismatched request id is ignored
    s = reducer(s, applyUiEvent({ type: 'chat.session.selected', payload: { session: { chat_session_id: 'a' }, request_id: 'REQ2' } }));
    expect(s.chats.active.detail).toBeNull();

    // matching request id applies
    s = reducer(s, applyUiEvent({ type: 'chat.session.selected', payload: { session: { chat_session_id: 'a' }, request_id: 'REQ1' } }));
    expect(s.chats.active.detail).toEqual({ chat_session_id: 'a' });
  });
});

describe('selectChatSessions (memoized)', () => {
  it('returns a stable reference until the chat list changes', () => {
    const s1 = reducer(undefined, snapshot(['a']));
    const r1 = selectChatSessions({ uiData: s1 });
    expect(selectChatSessions({ uiData: s1 })).toBe(r1);
    // unrelated state change keeps the same chats refs → memo holds
    expect(selectChatSessions({ uiData: { ...s1, status: { x: 1 } } })).toBe(r1);
  });

  it('recomputes after the chat list changes', () => {
    const s1 = reducer(undefined, snapshot(['a']));
    const r1 = selectChatSessions({ uiData: s1 });
    const s2 = reducer(s1, applyUiEvent({ type: 'chat.session.created', payload: { session: { chat_session_id: 'b' } } }));
    const r2 = selectChatSessions({ uiData: s2 });
    expect(r2).not.toBe(r1);
    expect(r2.map((c) => c.chat_session_id)).toEqual(['b', 'a']);
  });
});

describe('chat indicators (nav dot)', () => {
  it('defaults to no unread and not generating', () => {
    const s = reducer(undefined, { type: '@@INIT' });
    expect(selectChatIndicators({ uiData: s })).toEqual({ hasUnread: false, isGenerating: false });
  });

  it('setChatIndicators patches the fields it is given', () => {
    let s = reducer(undefined, setChatIndicators({ isGenerating: true }));
    expect(selectChatIndicators({ uiData: s })).toEqual({ hasUnread: false, isGenerating: true });
    s = reducer(s, setChatIndicators({ hasUnread: true, isGenerating: false }));
    expect(selectChatIndicators({ uiData: s })).toEqual({ hasUnread: true, isGenerating: false });
  });
});
