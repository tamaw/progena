import { describe, it, expect } from 'vitest';
import {
  compareSessionItems,
  normalizeBackendChatMessage,
  buildChatSessionItems,
  findAdjacentSessionItem,
  shouldMaterializeBeforeSwitching,
  visibleGroupsForFilter,
  sidebarEmptyMessage,
} from './chatSessionsModel';
import { getAgentMentionContext } from './chatInputModel';
import { groupSessionItems } from './conversationModel';
import { formatDisplayToolCalls } from './messageDisplayModel';

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

  // Regression: this comparator existed and was unit-tested (above) but was
  // never actually called from ConversationPage's "Recent" sort, so toggling
  // it was a no-op - items kept whatever order chats.order last happened to
  // be in. This mirrors the fix: sort with compareSessionItems before
  // grouping, so within-bucket order reflects recency instead of arrival order.
  it('sorting with compareSessionItems before grouping puts each bucket in recency order', () => {
    const items = [
      { id: 'old', title: 'Old', bucket: 'today', pinned: false, activityAt: 1 },
      { id: 'new', title: 'New', bucket: 'today', pinned: false, activityAt: 100 },
      { id: 'mid', title: 'Mid', bucket: 'today', pinned: false, activityAt: 50 },
    ];
    const sorted = [...items].sort(compareSessionItems);
    const groups = groupSessionItems(sorted);
    const today = groups.find((group) => group.key === 'today');
    expect(today.items.map((item) => item.id)).toEqual(['new', 'mid', 'old']);
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

  it('preserves optional assistant display metadata', () => {
    const m = normalizeBackendChatMessage({
      id: 'm3',
      role: 'assistant',
      content: 'hello',
      display_agent_name: 'greetings_agent',
      display_model_name: 'gpt-5-mini',
      display_tool_calls: ['sleep'],
      raw: { response_metadata: {} },
    }, 0);
    expect(m.displayAgentName).toBe('greetings_agent');
    expect(m.displayModelName).toBe('gpt-5-mini');
    expect(m.displayToolCalls).toEqual(['sleep']);
    expect(m.model).toBe('gpt-5-mini');
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

  it('carries the tool call created_at onto both synthesized events as a real timestamp', () => {
    const m = normalizeBackendChatMessage({
      id: 'm4',
      role: 'assistant',
      content: 'done',
      tool_calls: [
        { id: 't1', name: 'search', args: {}, result: 'res', created_at: '2026-07-11T02:31:08+00:00' },
      ],
    }, 1);
    const expected = new Date('2026-07-11T02:31:08+00:00').getTime();
    expect(m.streamEvents[0].timestamp).toBe(expected);
    expect(m.streamEvents[1].timestamp).toBe(expected);
  });

  it('leaves timestamp undefined when the tool call has no created_at', () => {
    const m = normalizeBackendChatMessage({
      id: 'm5',
      role: 'assistant',
      content: 'done',
      tool_calls: [{ id: 't1', name: 'search', args: {}, result: 'res' }],
    }, 1);
    expect(m.streamEvents[0].timestamp).toBeUndefined();
  });
});

describe('buildChatSessionItems', () => {
  const base = {
    messages: [],
    threadId: null,
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
        { thread_id: 'c1', title: 'One' },
        { thread_id: 'c2', title: 'Two' },
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
        { thread_id: 'c1', title: 'One' },
        { thread_id: 'c2', title: 'Two' },
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
      threadId: 'sess1',
      firstMessageTime: 1,
      selectedBackendChatId: null,
      backendChatSessions: [
        { thread_id: 'sess1', title: 'dup' },
        { thread_id: 'other', title: 'other' },
      ],
    });
    expect(items.some((i) => i.source === 'local' && i.id === 'sess1')).toBe(true);
    expect(items.filter((i) => i.source === 'backend').map((i) => i.id)).toEqual(['other']);
  });

  it('does not synthesize an empty local draft card anymore', () => {
    const items = buildChatSessionItems({
      ...base,
      threadId: 'draft-1',
      chatTitle: 'New chat',
      selectedBackendChatId: null,
      backendChatSessions: [
        { thread_id: 'older', title: 'Earlier chat' },
      ],
    });
    expect(items).toEqual([
      expect.objectContaining({
        id: 'older',
        source: 'backend',
      }),
    ]);
  });

  // Regression: locally-archived sessions used to be gated behind the same
  // "is there a live draft to show" flag as the current-session card, so
  // selecting ANY backend chat (setting selectedBackendChatId) made every
  // archived chat vanish from the list - which also fed into
  // usePrunedSelection and permanently unpinned/unshared them.
  it('keeps locally-archived sessions visible once a backend chat is selected', () => {
    const items = buildChatSessionItems({
      ...base,
      messages: [{ role: 'user', content: 'hi', timestamp: 1 }],
      threadId: 'sess1',
      selectedBackendChatId: 'c1',
      archivedSessions: [
        { id: 'archived-1', threadId: 'archived-1', title: 'Old chat', lastActivityAt: 1 },
      ],
      backendChatSessions: [
        { thread_id: 'c1', title: 'One' },
      ],
    });
    expect(items.some((i) => i.source === 'local' && i.id === 'archived-1')).toBe(true);
    // The transient current-draft card is still correctly hidden.
    expect(items.some((i) => i.source === 'local' && i.id === 'sess1')).toBe(false);
  });
});

describe('getAgentMentionContext', () => {
  it('detects an @mention token at the cursor', () => {
    expect(getAgentMentionContext('@greet', 6)).toEqual({
      query: 'greet',
      tokenStart: 0,
      tokenEnd: 6,
    });
  });

  it('returns null when the cursor is not inside a mention token', () => {
    expect(getAgentMentionContext('hello @greet there', 18)).toBeNull();
  });
});

describe('formatDisplayToolCalls', () => {
  it('renders string and object tool names compactly', () => {
    expect(formatDisplayToolCalls(['sleep', { name: 'probe_copilot' }])).toBe('sleep, probe_copilot');
  });

  it('omits output when metadata is absent', () => {
    expect(formatDisplayToolCalls([])).toBe('');
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

describe('findAdjacentSessionItem', () => {
  const items = [{ id: 'a' }, { id: 'b' }, { id: 'c' }];

  it('picks the next item after the removed one', () => {
    expect(findAdjacentSessionItem(items, 'a')).toEqual({ id: 'b' });
  });

  it('falls back to the previous item when removing the last one', () => {
    expect(findAdjacentSessionItem(items, 'c')).toEqual({ id: 'b' });
  });

  it('returns null when the removed id is not in the list', () => {
    expect(findAdjacentSessionItem(items, 'missing')).toBeNull();
  });

  it('returns null for a single-item list', () => {
    expect(findAdjacentSessionItem([{ id: 'only' }], 'only')).toBeNull();
  });
});

describe('shouldMaterializeBeforeSwitching', () => {
  it('materializes a local session with messages that has no backend selection yet', () => {
    expect(shouldMaterializeBeforeSwitching({
      selectedBackendChatId: null,
      threadId: 'sess1',
      messageCount: 2,
    })).toBe(true);
  });

  it('does not materialize once a backend chat is already selected', () => {
    expect(shouldMaterializeBeforeSwitching({
      selectedBackendChatId: 'c1',
      threadId: 'sess1',
      messageCount: 2,
    })).toBe(false);
  });

  it('does not materialize an empty session', () => {
    expect(shouldMaterializeBeforeSwitching({
      selectedBackendChatId: null,
      threadId: 'sess1',
      messageCount: 0,
    })).toBe(false);
  });

  it('does not materialize without a thread id', () => {
    expect(shouldMaterializeBeforeSwitching({
      selectedBackendChatId: null,
      threadId: null,
      messageCount: 2,
    })).toBe(false);
  });
});

describe('sidebarEmptyMessage', () => {
  it('returns the right message per state', () => {
    expect(sidebarEmptyMessage({ sidebarFilter: 'all', backendChatsLoaded: false, backendChatsLoading: false })).toBe('Loading chats...');
    expect(sidebarEmptyMessage({ sidebarFilter: 'unread', backendChatsLoaded: true, backendChatsLoading: false })).toBe('No unread chats.');
    expect(sidebarEmptyMessage({ sidebarFilter: 'shared', backendChatsLoaded: true, backendChatsLoading: false })).toBe('No shared chats yet.');
    expect(sidebarEmptyMessage({ sidebarFilter: 'archived', backendChatsLoaded: true, backendChatsLoading: false })).toBe('No archived chats yet.');
    expect(sidebarEmptyMessage({ sidebarFilter: 'all', backendChatsLoaded: true, backendChatsLoading: false })).toBe('No conversations.');
  });
});
