import { describe, expect, it } from 'vitest';
import { createLiveAssistantTurnStore } from '@frontend/features/chat/models/liveAssistantTurnStore';

describe('createLiveAssistantTurnStore', () => {
  it('starts with a null snapshot', () => {
    const store = createLiveAssistantTurnStore();

    expect(store.getSnapshot()).toBeNull();
  });

  it('replaces the snapshot and notifies each active subscriber once for a new turn', () => {
    const store = createLiveAssistantTurnStore();
    const firstSubscriberSnapshots = [];
    const secondSubscriberSnapshots = [];
    const nextTurn = { streamId: 'stream-001', content: 'Streaming reply' };

    store.subscribe(() => firstSubscriberSnapshots.push(store.getSnapshot()));
    store.subscribe(() => secondSubscriberSnapshots.push(store.getSnapshot()));
    store.set(nextTurn);

    expect(store.getSnapshot()).toBe(nextTurn);
    expect(firstSubscriberSnapshots).toHaveLength(1);
    expect(firstSubscriberSnapshots[0]).toBe(nextTurn);
    expect(secondSubscriberSnapshots).toHaveLength(1);
    expect(secondSubscriberSnapshots[0]).toBe(nextTurn);
  });

  it('does not notify subscribers when setting the same turn object', () => {
    const store = createLiveAssistantTurnStore();
    const turn = { streamId: 'stream-001', content: 'Unchanged reply' };
    const receivedSnapshots = [];

    store.set(turn);
    store.subscribe(() => receivedSnapshots.push(store.getSnapshot()));
    store.set(turn);

    expect(receivedSnapshots).toEqual([]);
    expect(store.getSnapshot()).toBe(turn);
  });

  it('does not notify an unsubscribed listener about later turns', () => {
    const store = createLiveAssistantTurnStore();
    const receivedSnapshots = [];
    const firstTurn = { streamId: 'stream-001', content: 'First reply' };
    const laterTurn = { streamId: 'stream-001', content: 'Later reply' };
    const unsubscribe = store.subscribe(() => receivedSnapshots.push(store.getSnapshot()));

    store.set(firstTurn);
    unsubscribe();
    store.set(laterTurn);

    expect(receivedSnapshots).toEqual([firstTurn]);
    expect(store.getSnapshot()).toBe(laterTurn);
  });

  it('notifies header subscribers only when live reasoning metadata changes', () => {
    const store = createLiveAssistantTurnStore();
    const headerSnapshots = [];
    const firstTurn = {
      streamId: 'stream-001',
      assistantMessageId: 'assistant-001',
      content: 'First visible text',
      reasoningTokenCount: 0,
      reasoningText: 'Checking the relevant evidence.',
      timelineItems: [{
        kind: 'stream_event',
        event: { eventType: 'reasoning_delta', content: 'Checking the relevant evidence.' },
      }],
    };

    store.subscribeHeader(() => headerSnapshots.push(store.getHeaderSnapshot()));
    store.set(firstTurn);

    const initialHeaderSnapshot = store.getHeaderSnapshot();
    expect(headerSnapshots).toEqual([initialHeaderSnapshot]);

    const textOnlyTurn = { ...firstTurn, content: 'First visible text, extended.' };
    store.set(textOnlyTurn);

    expect(headerSnapshots).toHaveLength(1);
    expect(store.getHeaderSnapshot()).toBe(initialHeaderSnapshot);

    const tokenUpdate = { ...textOnlyTurn, reasoningTokenCount: 8 };
    store.set(tokenUpdate);
    expect(headerSnapshots).toHaveLength(2);
    expect(headerSnapshots[1]).not.toBe(initialHeaderSnapshot);

    const reasoningTextUpdate = {
      ...tokenUpdate,
      reasoningText: 'Checked the relevant evidence.',
      timelineItems: [{
        kind: 'stream_event',
        event: { eventType: 'reasoning_delta', content: 'Checked the relevant evidence.' },
      }],
    };
    store.set(reasoningTextUpdate);
    expect(headerSnapshots).toHaveLength(3);

    const reasoningEventCountUpdate = {
      ...reasoningTextUpdate,
      timelineItems: [{
        kind: 'stream_event',
        event: { eventType: 'reasoning_summary', content: 'Checked the relevant evidence.' },
      }],
    };
    store.set(reasoningEventCountUpdate);

    expect(headerSnapshots).toHaveLength(4);
    expect(headerSnapshots.at(-1).reasoningEventCount).toBe(1);
  });

  it('notifies context-usage subscribers only when the latest-call footprint changes', () => {
    const store = createLiveAssistantTurnStore();
    const snapshots = [];
    const turn = {
      streamId: 'stream-001',
      content: 'First visible text',
      contextUsage: {
        inputTokens: 100,
        outputTokens: 20,
        totalTokens: 120,
        profileName: 'general',
      },
    };

    store.subscribeContextUsage(() => snapshots.push(store.getContextUsageSnapshot()));
    store.set(turn);
    store.set({ ...turn, content: 'Extended text' });
    store.set({ ...turn, contextUsage: { ...turn.contextUsage, totalTokens: 140 } });

    expect(snapshots).toEqual([
      turn.contextUsage,
      { ...turn.contextUsage, totalTokens: 140 },
    ]);
  });
});
