import { describe, it, expect } from 'vitest';
import {
  CHAT_AGENT_NAME,
  DEFAULT_CHAT_TITLE,
  shouldShowChatContentLoading,
  shouldShowNewChatEmptyState,
  toValidTimestamp,
  deriveChatTitle,
  collectMessageImages,
  countToolInvocations,
  countReasoningTokens,
  committedReasoningTokenDisplay,
  committedReasoningTokenCounts,
  liveReasoningTokenAdjustment,
  countTokens,
  getTimeBucket,
  groupSessionItems,
  getActiveRouteThreadId,
} from './conversationModel';

describe('chat content loading state', () => {
  it('shows loading while a selected chat detail request is in flight', () => {
    expect(shouldShowChatContentLoading({
      backendChatsLoaded: true,
      messages: [],
      selectedBackendChatId: 'chat-1',
      activeChatLoading: true,
      activeChatThreadId: 'chat-1',
      activeChatDetailThreadId: 'chat-1',
    })).toBe(true);
  });

  it('shows loading until the selected chat thread and detail match', () => {
    const baseState = {
      backendChatsLoaded: true,
      messages: [],
      selectedBackendChatId: 'chat-1',
      activeChatLoading: false,
    };

    expect(shouldShowChatContentLoading({
      ...baseState,
      activeChatThreadId: 'chat-2',
      activeChatDetailThreadId: 'chat-1',
    })).toBe(true);
    expect(shouldShowChatContentLoading({
      ...baseState,
      activeChatThreadId: 'chat-1',
      activeChatDetailThreadId: 'chat-2',
    })).toBe(true);
  });

  it('shows loading during the initial unselected roster load', () => {
    expect(shouldShowChatContentLoading({
      backendChatsLoaded: false,
      messages: [],
      selectedBackendChatId: null,
      activeChatLoading: false,
      activeChatThreadId: null,
      activeChatDetailThreadId: null,
    })).toBe(true);
  });
  it('keeps a URL-selected chat loading while initial selection is pending', () => {
    const pendingState = {
      backendChatsLoaded: true,
      messages: [],
      selectedBackendChatId: null,
      activeChatLoading: false,
      activeChatThreadId: null,
      activeChatDetailThreadId: null,
      urlThreadId: 'url-chat',
      initialSelectionPending: true,
    };

    expect(shouldShowChatContentLoading(pendingState)).toBe(true);
    expect(shouldShowChatContentLoading({
      ...pendingState,
      selectedBackendChatId: 'draft-chat',
      activeChatThreadId: 'draft-chat',
      activeChatDetailThreadId: 'draft-chat',
    })).toBe(true);
    expect(shouldShowChatContentLoading({
      ...pendingState,
      selectedBackendChatId: 'url-chat',
      activeChatThreadId: 'url-chat',
      activeChatDetailThreadId: 'url-chat',
      initialSelectionPending: false,
    })).toBe(false);
  });
});

describe('getActiveRouteThreadId', () => {
  it('preserves a URL-selected thread while initial selection is pending', () => {
    expect(getActiveRouteThreadId({
      selectedBackendChatId: null,
      messages: [],
      threadId: null,
      urlThreadId: 'url-thread',
      initialSelectionPending: true,
    })).toBe('url-thread');
  });

  it('prefers the selected backend thread over the pending URL thread', () => {
    expect(getActiveRouteThreadId({
      selectedBackendChatId: 'backend-thread',
      messages: [],
      threadId: null,
      urlThreadId: 'url-thread',
      initialSelectionPending: true,
    })).toBe('backend-thread');
  });

  it('uses a non-empty local thread when no backend thread is selected', () => {
    expect(getActiveRouteThreadId({
      selectedBackendChatId: null,
      messages: [{ role: 'user', content: 'hello' }],
      threadId: 'local-thread',
      urlThreadId: 'url-thread',
      initialSelectionPending: true,
    })).toBe('local-thread');
  });

  it('returns null after initial selection completes without selected or local content', () => {
    expect(getActiveRouteThreadId({
      selectedBackendChatId: null,
      messages: [],
      threadId: null,
      urlThreadId: 'url-thread',
      initialSelectionPending: false,
    })).toBeNull();
  });

  it('does not let empty messages override a pending URL thread', () => {
    expect(getActiveRouteThreadId({
      selectedBackendChatId: null,
      messages: [],
      threadId: 'local-thread',
      urlThreadId: 'url-thread',
      initialSelectionPending: true,
    })).toBe('url-thread');
  });
});

describe('new-chat empty state', () => {
  it('allows suggestions for a selected loaded chat with no messages', () => {
    const chatState = {
      messages: [],
      selectedBackendChatId: 'chat-1',
      threadId: 'chat-1',
    };
    expect(shouldShowChatContentLoading({
      ...chatState,
      backendChatsLoaded: true,
      activeChatLoading: false,
      activeChatThreadId: 'chat-1',
      activeChatDetailThreadId: 'chat-1',
    })).toBe(false);
    expect(shouldShowNewChatEmptyState(chatState)).toBe(true);
  });

  it('does not show suggestions for unselected empty messages', () => {
    expect(shouldShowNewChatEmptyState({
      messages: [],
      selectedBackendChatId: null,
      threadId: null,
    })).toBe(false);
  });
});

describe('toValidTimestamp', () => {
  it('passes finite numbers through', () => {
    expect(toValidTimestamp(1700000000000)).toBe(1700000000000);
  });
  it('parses date strings', () => {
    expect(toValidTimestamp('2026-06-04T00:00:00Z')).toBe(Date.parse('2026-06-04T00:00:00Z'));
  });
  it('returns null for invalid input', () => {
    expect(toValidTimestamp('not-a-date')).toBeNull();
    expect(toValidTimestamp({})).toBeNull();
    expect(toValidTimestamp(undefined)).toBeNull();
  });
});

describe('deriveChatTitle', () => {
  it('defaults when there is no user message', () => {
    expect(deriveChatTitle([])).toBe(DEFAULT_CHAT_TITLE);
    expect(deriveChatTitle([{ role: 'assistant', content: 'hi' }])).toBe(DEFAULT_CHAT_TITLE);
  });
  it('uses the first user message, whitespace-collapsed', () => {
    expect(deriveChatTitle([{ role: 'user', content: '  hello   world ' }])).toBe('hello world');
  });
  it('truncates long titles to 69 chars + ellipsis', () => {
    const long = 'x'.repeat(100);
    const title = deriveChatTitle([{ role: 'user', content: long }]);
    expect(title).toHaveLength(72);
    expect(title.endsWith('...')).toBe(true);
  });
});

describe('countToolInvocations', () => {
  it('counts tool_start events across messages', () => {
    const messages = [
      { streamEvents: [{ eventType: 'tool_start' }, { eventType: 'tool_result' }] },
      { streamEvents: [{ eventType: 'tool_start' }, { eventType: 'reasoning_delta' }] },
      { streamEvents: [] },
      {},
    ];
    expect(countToolInvocations(messages)).toBe(2);
  });
});

describe('countTokens', () => {
  it('sums finite token counts from every message and ignores invalid values', () => {
    const messages = [
      { role: 'assistant', tokenCount: 4 },
      { role: 'assistant', tokenCount: 9 },
      { role: 'assistant', tokenCount: 0 },
      { role: 'assistant', tokenCount: '3' },
      {},
      { role: 'assistant', tokenCount: NaN },
      { role: 'assistant', tokenCount: Infinity },
      { role: 'assistant', tokenCount: -Infinity },
      { role: 'assistant', tokenCount: 2 },
    ];

    expect(countTokens(messages)).toBe(15);
  });
});

describe('countReasoningTokens', () => {
  it('sums distinct finite reasoning totals across assistant messages', () => {
    const messages = [
      { role: 'assistant', reasoningTokenCount: 4 },
      { role: 'assistant', reasoningTokenCount: 9 },
      { role: 'assistant', reasoningTokenCount: 0 },
      { role: 'assistant', reasoningTokenCount: '3' },
      {},
      { role: 'assistant', reasoningTokenCount: NaN },
      { role: 'assistant', reasoningTokenCount: Infinity },
      { role: 'assistant', reasoningTokenCount: -Infinity },
      { role: 'assistant', reasoningTokenCount: 2 },
    ];

    expect(countReasoningTokens(messages)).toBe(15);
  });
  it('ignores missing, zero, invalid, and non-assistant reasoning counts', () => {
    const messages = [
      { role: 'assistant', reasoningTokenCount: 0 },
      { role: 'assistant' },
      { role: 'assistant', reasoningTokenCount: NaN },
      { role: 'user', reasoningTokenCount: 99 },
    ];

    expect(countReasoningTokens(messages)).toBe(0);
  });

  it('sums provider reasoning counts across assistant messages', () => {
    const messages = [
      { role: 'assistant', reasoningTokenCount: 64 },
      { role: 'user', reasoningTokenCount: 99 },
      { role: 'assistant', reasoningTokenCount: 128 },
    ];

    expect(countReasoningTokens(messages)).toBe(192);
  });
});

describe('committed and live reasoning token totals', () => {
  it('combines explicit committed counts with text estimates for missing counts', () => {
    const messages = [
      { id: 'explicit', role: 'assistant', reasoningTokenCount: 7, reasoningText: 'ignored fallback' },
      { id: 'estimated', role: 'assistant', reasoningText: '12345678' },
      { id: 'zero-count', role: 'ai', reasoningTokenCount: 0, reasoningText: 'abcd' },
      { id: 'user', role: 'user', reasoningTokenCount: 99, reasoningText: 'not included' },
    ];

    expect(committedReasoningTokenDisplay(messages)).toEqual({ total: 10, estimated: true });
  });

  it('keeps finite counts for identified assistant messages only', () => {
    const counts = committedReasoningTokenCounts([
      { id: 'assistant-1', role: 'assistant', reasoningTokenCount: 5 },
      { id: 'ai-1', role: 'ai', reasoningTokenCount: 3 },
      { id: 'user-1', role: 'user', reasoningTokenCount: 99 },
      { id: 'missing', role: 'assistant' },
      { id: 'invalid', role: 'assistant', reasoningTokenCount: NaN },
      { role: 'assistant', reasoningTokenCount: 8 },
    ]);

    expect(counts).toEqual(new Map([
      ['assistant-1', 5],
      ['ai-1', 3],
    ]));
  });

  it('subtracts the matching committed assistant count from the live total', () => {
    const committedCounts = committedReasoningTokenCounts([
      { id: 'assistant-1', role: 'assistant', reasoningTokenCount: 5 },
    ]);

    expect(liveReasoningTokenAdjustment({
      assistantMessageId: 'assistant-1',
      reasoningTokenCount: 8,
      reasoningText: 'already persisted reasoning',
    }, committedCounts)).toEqual({ total: 3, estimated: false });
  });

  it('estimates live reasoning text when no matching committed message exists', () => {
    const committedCounts = committedReasoningTokenCounts([
      { id: 'other-assistant', role: 'assistant', reasoningTokenCount: 4 },
    ]);

    expect(liveReasoningTokenAdjustment({
      assistantMessageId: 'new-assistant',
      reasoningTokenCount: 0,
      reasoningText: '  1234567890  ',
    }, committedCounts)).toEqual({ total: 3, estimated: true });
  });

  it('returns a zero adjustment when there is no live turn', () => {
    expect(liveReasoningTokenAdjustment(null, new Map())).toEqual({ total: 0, estimated: false });
  });
});

describe('collectMessageImages', () => {
  it('collects tool-result images only from assistant messages', () => {
    const userMessage = {
      role: 'user',
      content: 'hi',
      streamEvents: [{ eventType: 'tool_result', images: [{ base64: 'x', media_type: 'image/png' }] }],
    };
    expect(collectMessageImages(userMessage)).toEqual([]);
  });

  it('builds a data: URI per tool-result image, in event order', () => {
    const message = {
      role: 'assistant',
      content: 'here you go',
      streamEvents: [
        { eventType: 'tool_result', images: [{ id: 'a', base64: 'AAA', media_type: 'image/png' }] },
        { eventType: 'tool_result', images: [] },
        { eventType: 'tool_result', images: [{ id: 'b', base64: 'BBB' }] },
      ],
    };
    expect(collectMessageImages(message)).toEqual([
      { id: 'a', src: 'data:image/png;base64,AAA', alt: 'Image 1' },
      { id: 'b', src: 'data:image/jpeg;base64,BBB', alt: 'Image 2' },
    ]);
  });

  it('returns an empty list for a message with no images anywhere', () => {
    const message = { role: 'assistant', content: 'just text, no images', streamEvents: [] };
    expect(collectMessageImages(message)).toEqual([]);
  });

  it('tolerates a missing streamEvents array', () => {
    expect(collectMessageImages({ role: 'assistant', content: 'hi' })).toEqual([]);
  });

  // Regression: a `task` subagent delegation's own images ride on
  // subagent_result (see _expand_task_subagent_events in
  // deepagent_stream.py, which fully replaces the raw tool_result event
  // with this one), not tool_result -- a filter that only recognized
  // tool_result silently dropped them from both the image strip and the
  // Images filter count.
  it('also collects images from subagent_result events', () => {
    const message = {
      role: 'assistant',
      content: 'delegated',
      streamEvents: [
        { eventType: 'subagent_result', images: [{ id: 'a', base64: 'AAA', media_type: 'image/png' }] },
      ],
    };
    expect(collectMessageImages(message)).toEqual([
      { id: 'a', src: 'data:image/png;base64,AAA', alt: 'Image 1' },
    ]);
  });
  it('prefers a persisted image URL over an absent or stale base64 payload', () => {
    const image = collectMessageImages({
      role: 'assistant',
      content: 'generated image',
      streamEvents: [{
        eventType: 'tool_result',
        images: [{
          id: 'remote-image',
          url: 'https://cdn.example.test/generated.png',
          base64: 'stale-or-empty',
          media_type: 'image/png',
        }],
      }],
    });

    expect(image).toEqual([{
      id: 'remote-image',
      src: 'https://cdn.example.test/generated.png',
      alt: 'Image 1',
    }]);
  });

});

describe('getTimeBucket', () => {
  const now = new Date(2026, 5, 4, 12, 0, 0).getTime(); // local 2026-06-04 12:00
  const startOfToday = new Date(2026, 5, 4, 0, 0, 0).getTime();
  const day = 24 * 60 * 60 * 1000;

  it('classifies relative to the start of today', () => {
    expect(getTimeBucket(now, now)).toBe('today');
    expect(getTimeBucket(startOfToday - 1, now)).toBe('yesterday');
    expect(getTimeBucket(startOfToday - 3 * day, now)).toBe('last-week');
    expect(getTimeBucket(startOfToday - 10 * day, now)).toBe('earlier');
  });

  it('treats missing timestamps as today', () => {
    expect(getTimeBucket(null, now)).toBe('today');
  });
});

describe('groupSessionItems', () => {
  it('buckets items and keeps SIDEBAR order, omitting empty groups', () => {
    const groups = groupSessionItems([
      { id: 'p', pinned: true, bucket: 'earlier' },
      { id: 't', pinned: false, bucket: 'today' },
      { id: 'e', pinned: false, bucket: 'earlier' },
    ]);
    expect(groups.map((g) => g.key)).toEqual(['pinned', 'today', 'earlier']);
    expect(groups[0].items.map((i) => i.id)).toEqual(['p']);
  });
});

describe('CHAT_AGENT_NAME', () => {
  it('is Progenitor', () => {
    expect(CHAT_AGENT_NAME).toBe('Progenitor');
  });
});

describe('DEFAULT_CHAT_TITLE', () => {
  it('keeps empty draft conversations consistent across the page', () => {
    expect(DEFAULT_CHAT_TITLE).toBe('New chat');
  });
});
