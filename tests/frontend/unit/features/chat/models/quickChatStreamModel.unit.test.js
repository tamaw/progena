import { describe, expect, it } from 'vitest';
import { loadQuickChatHistory, normalizeQuickChatHistoryMessage, toQuickChatInputHistory } from '@frontend/features/chat/models/quickChatStreamModel';

describe('Quick Chat rich history normalization', () => {
  it('hides page context from a user row while retaining it for the next backend input', () => {
    const rawContent = '```page-context\n{"page":"inbox","id":"msg_1"}\n```\n\nApprove this.';
    const message = normalizeQuickChatHistoryMessage({
      id: 'user-1',
      role: 'user',
      content: rawContent,
      created_at: '2026-10-25T12:00:00Z',
    }, 0);

    expect(message.content).toBe('Approve this.');
    expect(toQuickChatInputHistory([message])).toEqual([{ role: 'user', content: rawContent }]);
  });

  it('uses the main chat normalizer so rehydrated tool work remains renderable', () => {
    const message = normalizeQuickChatHistoryMessage({
      id: 'assistant-1',
      role: 'assistant',
      content: 'Done.',
      created_at: '2026-10-25T12:00:01Z',
      tool_calls: [{
        id: 'call-1',
        name: 'search',
        args: { query: 'status' },
        result: 'found',
        created_at: '2026-10-25T12:00:00Z',
      }],
      context_usage: {
        input_tokens: 20,
        output_tokens: 5,
        total_tokens: 25,
        profile_name: 'default',
      },
    }, 1);

    expect(message.streamEvents.map((event) => event.eventType)).toEqual(['tool_start', 'tool_result']);
    expect(message.timelineItems.some((item) => item.kind === 'stream_event')).toBe(true);
    expect(message.contextUsage).toMatchObject({ inputTokens: 20, totalTokens: 25 });
  });
});

describe('Quick Chat history refresh', () => {
  it('retains every chronological turn across the 50-message page boundary', async () => {
    const messages = Array.from({ length: 72 }, (_,index) => ({
      id: `turn-${index}`, role: index % 2 ? 'assistant' : 'user', content: `Message ${index}`,
    }));
    const loaded = await loadQuickChatHistory(async (before) => {
      const end = before ? messages.findIndex((message) => message.id === before) : messages.length;
      const start = Math.max(0, end - 50);
      return { messages: messages.slice(start, end), has_more: start > 0 };
    }, () => true);
    expect(loaded.map((message) => message.id)).toEqual(messages.map((message) => message.id));
    expect(toQuickChatInputHistory(loaded)).toEqual(messages.map(({ role, content }) => ({ role, content })));
  });

  it('discards a delayed refresh after a newer submission completes', async () => {
    let epoch = 0;
    const capturedEpoch = epoch;
    let resolvePage;
    const page = new Promise((resolve) => { resolvePage = resolve; });
    const refresh = loadQuickChatHistory(() => page, () => epoch === capturedEpoch);
    // The active stream can return to idle; only the monotonic epoch identifies
    // that a whole newer turn has happened while the refresh was in flight.
    epoch += 1;
    resolvePage({ messages: [{ id: 'old', role: 'assistant', content: 'Old answer' }], has_more: false });
    expect(await refresh).toBeNull();
  });
});
