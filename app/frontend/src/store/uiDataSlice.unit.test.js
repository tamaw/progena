import { describe, it, expect } from 'vitest';
import reducer, {
  applyUiEvent,
  clearActiveChatSession,
  requestAgentDetail,
  requestChatSessionDetail,
  selectActiveAgentProfile,
  selectAgentProfiles,
  selectChatIndicators,
  selectChatSessions,
  selectSchedules,
  scheduleSnapshotReceived,
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

  it('clearActiveChatSession resets the active backend selection', () => {
    let s = reducer(undefined, requestChatSessionDetail.pending('rid', {
      chatsUrl: '/x', chatSessionId: 'a', requestId: 'REQ1',
    }));
    s = reducer(s, applyUiEvent({ type: 'chat.session.selected', payload: { session: { chat_session_id: 'a' }, request_id: 'REQ1' } }));
    s = reducer(s, clearActiveChatSession());
    expect(s.chats.active).toEqual({
      chatSessionId: null,
      detail: null,
      loading: false,
      error: null,
      requestId: null,
    });
  });

  it('schedule.snapshot replaces the schedules list', () => {
    const items = [{ id: 'sched-a', name: 'A' }, { id: 'sched-b', name: 'B', paused: true }];
    const s = reducer(undefined, applyUiEvent({ type: 'schedule.snapshot', payload: { items } }));
    expect(selectSchedules({ uiData: s })).toEqual(items);
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

const agentsSnapshot = (ids) => applyUiEvent({
  type: 'agents.snapshot',
  payload: { agents: ids.map((id) => ({ id, name: id })) },
});

describe('agent reducers', () => {
  it('agents.snapshot replaces the agent list and marks loaded', () => {
    const s = reducer(undefined, agentsSnapshot(['alpha', 'beta']));
    expect(s.agents.order).toEqual(['alpha', 'beta']);
    expect(s.agents.loaded).toBe(true);
    expect(selectAgentProfiles({ uiData: s }).map((a) => a.id)).toEqual(['alpha', 'beta']);
  });

  it('agent.upserted merges into an existing agent and appends new ones', () => {
    let s = reducer(undefined, agentsSnapshot(['alpha']));
    s = reducer(s, applyUiEvent({ type: 'agent.upserted', payload: { agent: { id: 'alpha', role: 'changed' } } }));
    expect(s.agents.order).toEqual(['alpha']);
    expect(s.agents.byId.alpha.role).toBe('changed');
    s = reducer(s, applyUiEvent({ type: 'agent.upserted', payload: { agent: { id: 'gamma' } } }));
    expect(s.agents.order).toEqual(['alpha', 'gamma']);
  });

  it('agent.selected applies only when request_id matches the active request', () => {
    let s = reducer(undefined, requestAgentDetail.pending('rid', {
      agentsUrl: '/x', agentId: 'alpha', requestId: 'REQ1',
    }));
    expect(s.agents.active.requestId).toBe('REQ1');
    s = reducer(s, applyUiEvent({ type: 'agent.selected', payload: { agent: { id: 'alpha' }, request_id: 'REQ2' } }));
    expect(selectActiveAgentProfile({ uiData: s })).toBeNull();
    s = reducer(s, applyUiEvent({ type: 'agent.selected', payload: { agent: { id: 'alpha' }, request_id: 'REQ1' } }));
    expect(selectActiveAgentProfile({ uiData: s })).toEqual({ id: 'alpha' });
  });

  it('agents.snapshot carries A2A fields (skills, url, transport) through to selectors', () => {
    const s = reducer(undefined, applyUiEvent({
      type: 'agents.snapshot',
      payload: {
        agents: [{
          id: 'a2a-observe',
          name: 'Observe Agent',
          kind: 'a2a',
          group: 'A2A agents',
          transport: 'JSONRPC',
          url: 'http://localhost:8080',
          card: {
            protocolVersion: '0.3.0',
            capabilities: { streaming: false, stateTransitionHistory: true },
            inputModes: ['text/plain'],
          },
          skills: [{ id: 'progenitor_health_report', name: 'Health', tags: ['observe'], examples: ['Run it'] }],
        }],
      },
    }));
    const agent = selectAgentProfiles({ uiData: s })[0];
    expect(agent.group).toBe('A2A agents');
    expect(agent.url).toBe('http://localhost:8080');
    expect(agent.transport).toBe('JSONRPC');
    expect(agent.card.protocolVersion).toBe('0.3.0');
    expect(agent.card.capabilities.stateTransitionHistory).toBe(true);
    expect(agent.skills.map((skill) => skill.id)).toEqual(['progenitor_health_report']);
    expect(agent.skills[0].examples).toEqual(['Run it']);
  });

  it('agent.graph.updated patches the active detail graph', () => {
    let s = reducer(undefined, agentsSnapshot(['alpha']));
    s = reducer(s, applyUiEvent({ type: 'agent.selected', payload: { agent: { id: 'alpha' } } }));
    const graph = { status: 'Designed', nodes: [{ id: 'a' }], edges: [] };
    s = reducer(s, applyUiEvent({ type: 'agent.graph.updated', payload: { agent_id: 'alpha', graph } }));
    expect(s.agents.active.detail.graph).toEqual(graph);
    expect(s.agents.byId.alpha.graph).toEqual(graph);
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

describe('scheduleSnapshotReceived', () => {
  it('stores the provided schedule list directly', () => {
    const items = [{ id: 'sched-live', name: 'Live schedule' }];
    const s = reducer(undefined, scheduleSnapshotReceived(items));
    expect(selectSchedules({ uiData: s })).toEqual(items);
  });
});
