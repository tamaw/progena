import { describe, expect, it, vi } from 'vitest';
import { loadQuickChatHandoff } from '@frontend/features/chat/models/chatStorage';
import {
  createQuickChatPromotion,
  persistQuickChatHandoff,
} from '@frontend/features/chat/models/quickChatPromotionModel';

describe('quick chat promotion', () => {
  it('keeps rich trace data in memory while storing one bounded, persistable handoff', () => {
    const image = 'data:image/png;base64,rich-renderer-image';
    const history = Array.from({ length: 101 }, (_, index) => ({
      id: `message-${index}`,
      role: 'assistant',
      content: `Message ${index}`,
      streamEvents: [{
        eventType: 'tool_result',
        metadata: { tool_call_id: `tool-${index}` },
        images: index === 100 ? [image] : undefined,
      }],
      timelineItems: [{ kind: 'stream_event', event: { images: [image] } }],
    }));
    const { handoff, persistedHandoff } = createQuickChatPromotion(history, 'thread-canonical');
    const storage = { setItem: vi.fn() };

    expect(handoff.messages).toHaveLength(101);
    expect(handoff.messages[100].streamEvents[0].images).toEqual([image]);
    expect(persistQuickChatHandoff('quick-handoff', persistedHandoff, storage)).toBe(true);
    expect(storage.setItem).toHaveBeenCalledTimes(1);

    const stored = JSON.parse(storage.setItem.mock.calls[0][1]);
    expect(stored.threadId).toBe('thread-canonical');
    expect(stored.messages).toHaveLength(100);
    expect(stored.messages[0].id).toBe('message-1');
    expect(stored.messages[99].streamEvents[0].images).toBeUndefined();
    expect(JSON.stringify(stored)).not.toContain(image);
    expect(handoff.messages[100].timelineItems[0].event.images).toEqual([image]);
  });

  it('round-trips only retained-window markers while keeping the live handoff complete', () => {
    const history = Array.from({ length: 101 }, (_, index) => ({
      id: `message-${index}`,
      role: 'user',
      content: `Message ${index}`,
    }));
    const markers = [
      {
        id: 'summary-pruned',
        anchor_message_id: 'message-0',
        summary: 'Pruned root context.',
        scope: { kind: 'conversation', id: 'thread-canonical' },
      },
      {
        id: 'summary-head',
        anchor_message_id: null,
        summary: 'Original transcript head.',
        scope: { kind: 'conversation', id: 'thread-canonical' },
      },
      {
        id: 'summary-retained-subagent',
        anchor_message_id: 'message-100',
        summary: 'Retained nested context.',
        scope: { kind: 'subagent', id: 'tool-100' },
      },
    ];
    const { handoff, persistedHandoff } = createQuickChatPromotion(
      history,
      'thread-canonical',
      markers,
    );

    expect(handoff.summaryMarkers).toEqual(markers);
    expect(persistQuickChatHandoff('quick-handoff', persistedHandoff)).toBe(true);
    expect(loadQuickChatHandoff('quick-handoff')).toEqual({
      messages: persistedHandoff.messages,
      threadId: 'thread-canonical',
      summaryMarkers: [{
        id: 'summary-retained-subagent',
        anchorMessageId: 'message-100',
        summary: 'Retained nested context.',
        scope: { kind: 'subagent', id: 'tool-100' },
      }],
    });
  });

  it('keeps a head marker when the persisted fallback retains the transcript head', () => {
    const headMarker = {
      id: 'summary-head',
      anchor_message_id: null,
      summary: 'Original transcript head.',
      scope: { kind: 'conversation', id: 'thread-canonical' },
    };
    const { persistedHandoff } = createQuickChatPromotion(
      [{ id: 'message-1', role: 'user', content: 'First message' }],
      'thread-canonical',
      [headMarker],
    );

    expect(persistedHandoff.summaryMarkers).toEqual([headMarker]);
  });

  it('does not block promotion when browser storage rejects the fallback', () => {
    const storage = {
      setItem() {
        throw new Error('QuotaExceededError');
      },
    };

    expect(
      persistQuickChatHandoff('quick-handoff', { messages: [], threadId: 'thread-canonical' }, storage),
    ).toBe(false);
  });
});
