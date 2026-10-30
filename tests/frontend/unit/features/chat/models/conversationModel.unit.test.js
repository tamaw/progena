import { describe, it, expect } from 'vitest';
import {
  shouldShowChatContentLoading,
  shouldShowNewChatEmptyState,
  toValidTimestamp,
  formatStartedTime,
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
  shouldDeferInitialRosterSelection,
  shouldDeferRouteSelection,
  shouldDismissEmptySelection,
  shouldSkipRoutedMountOpen,
  areChatTranscriptsEquivalent,
  shouldRetireLiveOwnerAfterCanonicalReconciliation,
  mergeCanonicalMessages,
  excludeLiveOwnedMessage,
  isReopeningCurrentLocalDraft,
  shouldAdoptMaterializedDraft,
} from '@frontend/features/chat/models/conversationModel';

describe('chat selection race guards', () => {
  it('defers roster selection until draft bootstrap settles or an existing selection exists', () => {
    expect(shouldDeferInitialRosterSelection({
      draftBootstrapSettled: false,
      selectedBackendChatId: null,
      threadId: null,
      messageCount: 0,
    })).toBe(true);
    expect(shouldDeferInitialRosterSelection({
      draftBootstrapSettled: true,
      selectedBackendChatId: 'draft-1',
      threadId: null,
      messageCount: 0,
    })).toBe(true);
    expect(shouldDeferInitialRosterSelection({
      draftBootstrapSettled: true,
      selectedBackendChatId: null,
      threadId: null,
      messageCount: 0,
    })).toBe(false);
  });

  it('defers route reconciliation while a different user selection is in flight', () => {
    expect(shouldDeferRouteSelection({
      pendingThreadId: null,
      selectionIntentThreadId: 'chat-b',
      urlThreadId: 'chat-a',
      selectedBackendChatId: 'chat-b',
    })).toBe(true);
    expect(shouldDeferRouteSelection({
      pendingThreadId: 'chat-b',
      selectionIntentThreadId: 'chat-b',
      urlThreadId: 'chat-b',
      selectedBackendChatId: 'chat-b',
    })).toBe(true);
    expect(shouldDeferRouteSelection({
      pendingThreadId: null,
      selectionIntentThreadId: null,
      urlThreadId: 'chat-a',
      selectedBackendChatId: null,
    })).toBe(false);
  });

  it('skips the routed mount open only when a request is in flight or the detail is already present', () => {
    // Request already in flight for the routed thread.
    expect(shouldSkipRoutedMountOpen({
      pendingThreadId: 'chat-a',
      selectedBackendChatId: 'chat-a',
      urlThreadId: 'chat-a',
      activeDetailThreadId: null,
      activeLoading: false,
      activeLoadingThreadId: null,
    })).toBe(true);
    // SPA revisit: detail for the routed thread is already loaded.
    expect(shouldSkipRoutedMountOpen({
      pendingThreadId: null,
      selectedBackendChatId: 'chat-a',
      urlThreadId: 'chat-a',
      activeDetailThreadId: 'chat-a',
      activeLoading: false,
      activeLoadingThreadId: null,
    })).toBe(true);
    // Detail request loading for the routed thread.
    expect(shouldSkipRoutedMountOpen({
      pendingThreadId: null,
      selectedBackendChatId: 'chat-a',
      urlThreadId: 'chat-a',
      activeDetailThreadId: null,
      activeLoading: true,
      activeLoadingThreadId: 'chat-a',
    })).toBe(true);
    // Regression: fresh page load — selection was initialized from the URL
    // but nothing is loading and no detail exists. The open MUST happen.
    expect(shouldSkipRoutedMountOpen({
      pendingThreadId: null,
      selectedBackendChatId: 'chat-a',
      urlThreadId: 'chat-a',
      activeDetailThreadId: null,
      activeLoading: false,
      activeLoadingThreadId: null,
    })).toBe(false);
    // Stale detail from a different thread does not count.
    expect(shouldSkipRoutedMountOpen({
      pendingThreadId: null,
      selectedBackendChatId: 'chat-a',
      urlThreadId: 'chat-a',
      activeDetailThreadId: 'chat-b',
      activeLoading: true,
      activeLoadingThreadId: 'chat-b',
    })).toBe(false);
  });

  it('dismisses an empty selection only after mount selection has run', () => {
    const emptySelection = {
      selectedBackendChatId: 'chat-a',
      activeLoading: false,
      activeDetail: null,
      activeError: null,
    };
    // Regression: URL-initialized selection before the mount effect has issued
    // any request must not be dismissed (it bounced /chat/<id> loads to /chat).
    expect(shouldDismissEmptySelection({ ...emptySelection, hasAutoSelectedOnMount: false })).toBe(false);
    expect(shouldDismissEmptySelection({ ...emptySelection, hasAutoSelectedOnMount: true })).toBe(true);
    // Loading, loaded, and errored selections are never dismissed.
    expect(shouldDismissEmptySelection({ ...emptySelection, hasAutoSelectedOnMount: true, activeLoading: true })).toBe(false);
    expect(shouldDismissEmptySelection({ ...emptySelection, hasAutoSelectedOnMount: true, activeDetail: { thread_id: 'chat-a' } })).toBe(false);
    expect(shouldDismissEmptySelection({ ...emptySelection, hasAutoSelectedOnMount: true, activeError: 'boom' })).toBe(false);
    // No selection at all.
    expect(shouldDismissEmptySelection({ ...emptySelection, hasAutoSelectedOnMount: true, selectedBackendChatId: null })).toBe(false);
    expect(shouldDismissEmptySelection({
      ...emptySelection,
      hasAutoSelectedOnMount: true,
      hasMaterializedDraft: true,
    })).toBe(false);
  });

  it('retains local state only when reopening the same local draft', () => {
    expect(isReopeningCurrentLocalDraft({
      selectedBackendChatId: null,
      currentThreadId: 'draft-a',
      requestedThreadId: 'draft-a',
    })).toBe(true);
    expect(isReopeningCurrentLocalDraft({
      selectedBackendChatId: null,
      currentThreadId: 'draft-a',
      requestedThreadId: 'draft-b',
    })).toBe(false);
    expect(isReopeningCurrentLocalDraft({
      selectedBackendChatId: 'chat-a',
      currentThreadId: 'draft-a',
      requestedThreadId: 'draft-a',
    })).toBe(false);
  });

  it('adopts a local draft when its session materializes in the roster', () => {
    expect(shouldAdoptMaterializedDraft({
      selectedBackendChatId: null,
      threadId: 'draft-a',
      sessionThreadId: 'draft-a',
    })).toBe(true);
    expect(shouldAdoptMaterializedDraft({
      selectedBackendChatId: null,
      threadId: 'draft-a',
      sessionThreadId: 'draft-b',
    })).toBe(false);
  });
});

describe('areChatTranscriptsEquivalent', () => {
  const localTranscript = [
    {
      id: 'local-user-id',
      role: 'user',
      content: 'Explain the handoff.',
      timestamp: 100,
      createdAt: 100,
      timelineIndex: 0,
    },
    {
      id: 'local-assistant-id',
      role: 'assistant',
      content: 'The final answer is visible.',
      timestamp: 200,
      createdAt: 200,
      timelineIndex: 1,
    },
  ];

  it('treats persisted transcript copies as equal when only identities and derived timeline fields differ', () => {
    const persistedTranscript = [
      {
        id: 'server-user-id',
        role: 'user',
        content: 'Explain the handoff.',
        timestamp: 1000,
        createdAt: 1000,
        timelineIndex: 19,
      },
      {
        id: 'server-assistant-id',
        role: 'assistant',
        content: 'The final answer is visible.',
        timestamp: 2000,
        createdAt: 2000,
        timelineIndex: 20,
      },
    ];

    expect(areChatTranscriptsEquivalent(localTranscript, persistedTranscript)).toBe(true);
  });

  it('detects a persisted final assistant-content difference', () => {
    const persistedTranscript = [
      { id: 'server-user-id', role: 'user', content: 'Explain the handoff.' },
      { id: 'server-assistant-id', role: 'assistant', content: 'The final answer is stale.' },
    ];

    expect(areChatTranscriptsEquivalent(localTranscript, persistedTranscript)).toBe(false);
  });

  it.each([
    {
      name: 'the persisted transcript has an extra message',
      persistedTranscript: [
        ...localTranscript,
        { id: 'server-extra-id', role: 'assistant', content: 'Unexpected extra turn.' },
      ],
    },
    {
      name: 'a persisted message changes role',
      persistedTranscript: [
        { id: 'server-user-id', role: 'assistant', content: 'Explain the handoff.' },
        { id: 'server-assistant-id', role: 'assistant', content: 'The final answer is visible.' },
      ],
    },
  ])('rejects reconciliation when $name', ({ persistedTranscript }) => {
    expect(areChatTranscriptsEquivalent(localTranscript, persistedTranscript)).toBe(false);
  });
});

describe('mergeCanonicalMessages', () => {
  const localMessages = [
    {
      id: 'local-user-id',
      role: 'user',
      content: 'Show the terminal output.',
      timestamp: 100,
      timelineIndex: 0,
    },
    {
      id: 'local-assistant-id',
      role: 'assistant',
      content: '```js\nconst answer = 42;\n```',
      timestamp: 200,
      timelineIndex: 1,
    },
  ];

  it('keeps rendering-equivalent row objects while replacing only a changed terminal row', () => {
    const canonicalMessages = [
      {
        id: 'canonical-user-id',
        role: 'user',
        content: 'Show the terminal output.',
        timestamp: 1000,
        timelineIndex: 10,
      },
      {
        id: 'canonical-assistant-id',
        role: 'assistant',
        content: '```js\nconst answer = 43;\n```',
        timestamp: 2000,
        timelineIndex: 11,
      },
    ];

    const merged = mergeCanonicalMessages(localMessages, canonicalMessages);

    expect(merged).not.toBe(localMessages);
    expect(merged[0]).toBe(localMessages[0]);
    expect(merged[1]).toBe(canonicalMessages[1]);
  });

  it('returns the original local array when every canonical row renders equivalently', () => {
    const canonicalMessages = localMessages.map((message, index) => ({
      ...message,
      id: `canonical-${index}`,
      timestamp: message.timestamp + 1000,
      timelineIndex: message.timelineIndex + 10,
    }));

    const merged = mergeCanonicalMessages(localMessages, canonicalMessages);

    expect(merged).toBe(localMessages);
    expect(merged[0]).toBe(localMessages[0]);
    expect(merged[1]).toBe(localMessages[1]);
  });

  it('replaces a same-content terminal row when canonical output accounting arrives', () => {
    const local = [{
      id: 'local-answer',
      role: 'assistant',
      content: 'Done',
      tokenCount: null,
      finalCallTokenCount: null,
      responseStatus: 'running',
      contextUsage: null,
    }];
    const canonical = [{
      id: 'canonical-answer',
      role: 'assistant',
      content: 'Done',
      tokenCount: 30,
      finalCallTokenCount: 8,
      responseStatus: 'completed',
      contextUsage: {
        inputTokens: 90,
        outputTokens: 8,
        totalTokens: 98,
        profileName: 'general',
        deployment: 'gpt-a',
        providerId: 'provider-a',
      },
    }];

    const merged = mergeCanonicalMessages(local, canonical);

    expect(merged).toEqual(canonical);
    expect(merged[0]).toBe(canonical[0]);
  });
});

describe('canonical assistant visual ownership', () => {
  it('shows exactly the backend canonical assistant row after non-equivalent reconciliation retires a completed client live owner', () => {
    const localMessages = [
      { id: 'client-user-id', role: 'user', content: 'Show the corrected answer.', timestamp: 100 },
      { id: 'client-assistant-id', role: 'assistant', content: 'Provisional answer.', timestamp: 200 },
    ];
    const canonicalMessages = [
      { id: 'backend-user-id', role: 'user', content: 'Show the corrected answer.', timestamp: 1000 },
      { id: 'backend-assistant-id', role: 'assistant', content: 'Corrected canonical answer.', timestamp: 2000 },
    ];
    const completedClientLiveTurn = {
      assistantMessageId: 'client-assistant-id',
      content: 'Provisional answer.',
      isStreaming: false,
      streamId: 'terminal-stream-id',
    };

    expect(shouldRetireLiveOwnerAfterCanonicalReconciliation(
      completedClientLiveTurn,
      'terminal-stream-id',
    )).toBe(true);
    expect(shouldRetireLiveOwnerAfterCanonicalReconciliation(
      completedClientLiveTurn,
      'different-stream-id',
    )).toBe(false);
    expect(shouldRetireLiveOwnerAfterCanonicalReconciliation(
      { ...completedClientLiveTurn, assistantMessageId: null },
      'terminal-stream-id',
    )).toBe(false);

    const reconciledMessages = mergeCanonicalMessages(localMessages, canonicalMessages);
    const retainedLiveOwner = shouldRetireLiveOwnerAfterCanonicalReconciliation(
      completedClientLiveTurn,
      'terminal-stream-id',
    ) ? null : completedClientLiveTurn;
    const visibleAssistantRows = [
      ...excludeLiveOwnedMessage(reconciledMessages, retainedLiveOwner?.assistantMessageId)
        .filter((message) => message.role === 'assistant'),
      ...(retainedLiveOwner ? [retainedLiveOwner] : []),
    ];

    expect(visibleAssistantRows).toEqual([canonicalMessages[1]]);
    expect(visibleAssistantRows).toHaveLength(1);
    expect(visibleAssistantRows[0].id).toBe('backend-assistant-id');
  });
});

describe('excludeLiveOwnedMessage', () => {
  const messages = [
    { id: 'user-1', role: 'user', content: 'Explain terminal ownership.' },
    { id: 'assistant-1', role: 'assistant', content: 'Settled live response.' },
    { id: 'assistant-2', role: 'assistant', content: 'Earlier response.' },
  ];

  it('suppresses the committed row while a matching finalized live turn owns it', () => {
    const visibleMessages = excludeLiveOwnedMessage(messages, 'assistant-1');

    expect(visibleMessages).toEqual([messages[0], messages[2]]);
    expect(visibleMessages).not.toBe(messages);
    expect(visibleMessages[0]).toBe(messages[0]);
    expect(visibleMessages[1]).toBe(messages[2]);
  });

  it.each([null, undefined, '', 'missing-live-owner'])(
    'returns the original transcript after the live owner is gone or does not match (%j)',
    (liveAssistantMessageId) => {
      expect(excludeLiveOwnedMessage(messages, liveAssistantMessageId)).toBe(messages);
    },
  );
});
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

  it('routes an empty local draft once initial selection completes', () => {
    expect(getActiveRouteThreadId({
      selectedBackendChatId: null,
      messages: [],
      threadId: 'draft-thread',
      urlThreadId: 'previous-chat',
      initialSelectionPending: false,
    })).toBe('draft-thread');
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

describe('formatStartedTime', () => {
  const now = Date.parse('2026-06-15T12:00:00Z');

  it('returns empty string for falsy/invalid input', () => {
    expect(formatStartedTime(null, now)).toBe('');
    expect(formatStartedTime(undefined, now)).toBe('');
    expect(formatStartedTime('not-a-date', now)).toBe('');
  });

  it('omits the date for a timestamp within the last week', () => {
    const threeDaysAgo = now - 3 * 86400000;
    const formatted = formatStartedTime(threeDaysAgo, now);
    expect(formatted).not.toMatch(/\d{1,2}\/\d{1,2}|Jun|June/);
  });

  it('includes a short date for a timestamp 7+ days old', () => {
    const eightDaysAgo = now - 8 * 86400000;
    const formatted = formatStartedTime(eightDaysAgo, now);
    expect(formatted).toMatch(/Jun/);
  });

  it('accepts an ISO date string, matching timestamp behavior', () => {
    const isoEightDaysAgo = new Date(now - 8 * 86400000).toISOString();
    expect(formatStartedTime(isoEightDaysAgo, now)).toMatch(/Jun/);
  });
});

describe('deriveChatTitle', () => {
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
    expect(getTimeBucket(startOfToday - 3 * day, now)).toBe('this-week');
    expect(getTimeBucket(startOfToday - 10 * day, now)).toBe('last-week');
    expect(getTimeBucket(startOfToday - 20 * day, now)).toBe('later');
  });

  it('treats missing timestamps as today', () => {
    expect(getTimeBucket(null, now)).toBe('today');
  });
});

describe('groupSessionItems', () => {
  it('buckets items and keeps SIDEBAR order, omitting empty groups', () => {
    const groups = groupSessionItems([
      { id: 'p', pinned: true, bucket: 'later' },
      { id: 't', pinned: false, bucket: 'today' },
      { id: 'w', pinned: false, bucket: 'this-week' },
      { id: 'e', pinned: false, bucket: 'later' },
    ]);
    expect(groups.map((g) => g.key)).toEqual(['pinned', 'today', 'this-week', 'later']);
    expect(groups[0].items.map((i) => i.id)).toEqual(['p']);
  });
});
