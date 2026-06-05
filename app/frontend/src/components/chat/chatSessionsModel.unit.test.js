import { describe, it, expect } from 'vitest';
import {
  compareSessionItems,
  normalizeBackendChatMessage,
  buildChatSessionItems,
  visibleGroupsForFilter,
  sidebarEmptyMessage,
} from './chatSessionsModel';
import { groupSessionItems } from './conversationModel';

describe('compareSessionItems', () => {
  it('orders pinned before unpinned', () => {
    expect(compareSessionItems(
      { pinned: true, activityAt: 1, id: 'a' },
      { pinned: false, activityAt: 100, id: 'b' },
    )).toBeLessThan(0);
  });
  it('orders by most-recent activity among unpinned', () => {
    expect(compareSessionItems(
      { pinned: false, activityAt: 1, id: 'a' },
      { pinned: false, activityAt: 2, id: 'b' },
    )).toBeGreaterThan(0);
  });
});

describe('normalizeBackendChatMessage', () => {
  it('returns null for non-objects', () => {
    expect(normalizeBackendChatMessage(null, 0)).toBeNull();
  });

  it('extracts the model from raw.response_metadata.model_name', () => {
    const m = normalizeBackendChatMessage({
      id: 'm1',
      role: 'assistant',
      content: 'hello',
      token_count: 5,
      raw: { response_metadata: { model_name: 'gpt-x' } },
    }, 0);
    expect(m.id).toBe('m1');
    expect(m.role).toBe('assistant');
    expect(m.content).toBe('hello');
    expect(m.tokenCount).toBe(5);
    expect(m.model).toBe('gpt-x');
  });

  it('expands tool_calls into tool_start + tool_result stream events', () => {
    const m = normalizeBackendChatMessage({
      id: 'm2',
      role: 'assistant',
      content: 'done',
      tool_calls: [{ id: 't1', name: 'search', args: { q: 'x' }, result: 'res' }],
    }, 1);
    const types = m.streamEvents.map((e) => e.eventType);
    expect(types).toEqual(['tool_start', 'tool_result']);
    expect(m.streamEvents[0].metadata.tool_name).toBe('search');
  });
});

describe('buildChatSessionItems', () => {
  const base = {
    messages: [],
    sessionId: null,
    chatTitle: 'x',
    totalTokenCount: 0,
    totalToolCount: 0,
    firstMessageTime: undefined,
    archivedSessions: [],
    pinnedSessionIds: [],
    sharedSessionIds: [],
    backendChatsLoaded: true,
    unreadChatIds: new Set(),
    backgroundGeneratingChatIds: new Set(),
    backgroundErrorChatIds: new Set(),
  };

  it('threads selected / unread / generating / errored flags onto backend cards', () => {
    const items = buildChatSessionItems({
      ...base,
      backendChatSessions: [
        { chat_session_id: 'c1', title: 'One' },
        { chat_session_id: 'c2', title: 'Two' },
      ],
      selectedBackendChatId: 'c1',
      unreadChatIds: new Set(['c2']),
      backgroundGeneratingChatIds: new Set(['c1']),
      backgroundErrorChatIds: new Set(['c2']),
    });
    const byId = Object.fromEntries(items.map((i) => [i.id, i]));
    expect(items.every((i) => i.source === 'backend')).toBe(true); // no local card when a backend chat is selected
    expect(byId.c1.selected).toBe(true);
    expect(byId.c1.generating).toBe(true);
    expect(byId.c2.unread).toBe(true);
    expect(byId.c2.erroredInBackground).toBe(true);
  });

  it('marks the selected card as generating while it streams in the foreground', () => {
    const items = buildChatSessionItems({
      ...base,
      backendChatSessions: [
        { chat_session_id: 'c1', title: 'One' },
        { chat_session_id: 'c2', title: 'Two' },
      ],
      selectedBackendChatId: 'c1',
      isGenerating: true,
    });
    const byId = Object.fromEntries(items.map((i) => [i.id, i]));
    // Foreground stream on the viewed chat shows the dot without ever entering
    // the background-generating set.
    expect(byId.c1.generating).toBe(true);
    expect(byId.c2.generating).toBe(false);
  });

  it('dedupes a backend card that matches the local current session', () => {
    const items = buildChatSessionItems({
      ...base,
      messages: [{ role: 'user', content: 'hi', timestamp: 1 }],
      sessionId: 'sess1',
      firstMessageTime: 1,
      selectedBackendChatId: null,
      backendChatSessions: [
        { chat_session_id: 'sess1', title: 'dup' },
        { chat_session_id: 'other', title: 'other' },
      ],
    });
    expect(items.some((i) => i.source === 'local' && i.id === 'sess1')).toBe(true);
    expect(items.filter((i) => i.source === 'backend').map((i) => i.id)).toEqual(['other']);
  });
});

describe('visibleGroupsForFilter', () => {
  const mk = (o) => ({
    id: o.id, title: o.id, summary: '', bucket: 'today', pinned: false,
    unread: !!o.unread, shared: !!o.shared, archived: !!o.archived,
  });
  const items = [
    mk({ id: 'u', unread: true }),
    mk({ id: 's', shared: true }),
    mk({ id: 'ar', archived: true }),
    mk({ id: 'uar', unread: true, archived: true }),
  ];

  it('unread filter excludes archived', () => {
    const groups = visibleGroupsForFilter({ sessionItems: items, sidebarFilter: 'unread', groupSessionItems });
    expect(groups.flatMap((g) => g.items.map((i) => i.id))).toEqual(['u']);
  });
  it('archived filter shows archived only', () => {
    const groups = visibleGroupsForFilter({ sessionItems: items, sidebarFilter: 'archived', groupSessionItems });
    expect(groups.flatMap((g) => g.items.map((i) => i.id)).sort()).toEqual(['ar', 'uar']);
  });
  it('all filter groups non-archived by time bucket', () => {
    const groups = visibleGroupsForFilter({ sessionItems: items, sidebarFilter: 'all', groupSessionItems });
    const today = groups.find((g) => g.key === 'today');
    expect(today.items.map((i) => i.id).sort()).toEqual(['s', 'u']);
  });
});

describe('sidebarEmptyMessage', () => {
  it('returns the right message per state', () => {
    expect(sidebarEmptyMessage({ sidebarFilter: 'all', backendChatsLoaded: false, backendChatsLoading: false })).toBe('Loading chats...');
    expect(sidebarEmptyMessage({ sidebarFilter: 'unread', backendChatsLoaded: true, backendChatsLoading: false })).toBe('No unread chats.');
    expect(sidebarEmptyMessage({ sidebarFilter: 'shared', backendChatsLoaded: true, backendChatsLoading: false })).toBe('No shared chats yet.');
    expect(sidebarEmptyMessage({ sidebarFilter: 'archived', backendChatsLoaded: true, backendChatsLoading: false })).toBe('No archived chats yet.');
    expect(sidebarEmptyMessage({ sidebarFilter: 'all', backendChatsLoaded: true, backendChatsLoading: false })).toBe('No chats yet.');
  });
});
