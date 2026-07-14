import { describe, expect, it } from 'vitest';
import { createLiveAssistantTurnStore } from './liveAssistantTurnStore';

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
});
