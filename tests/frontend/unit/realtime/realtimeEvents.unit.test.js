import { afterEach, describe, expect, it } from 'vitest';
import { publishUiEvent, subscribeUiEvent } from '@frontend/realtime/realtimeEvents';

let cleanups = [];

function subscribe(listener) {
  const unsubscribe = subscribeUiEvent(listener);
  cleanups.push(unsubscribe);
  return unsubscribe;
}

afterEach(() => {
  for (const unsubscribe of cleanups.reverse()) {
    unsubscribe();
  }
  cleanups = [];
});

describe('realtime event subscriptions', () => {
  it('delivers every published event to all active subscribers in publish order', () => {
    const firstSubscriberEvents = [];
    const secondSubscriberEvents = [];
    const events = [
      { id: 'ui-1', type: 'chat.response.delta' },
      { id: 'ui-2', type: 'chat.response.delta' },
      { id: 'ui-3', type: 'chat.response.completed' },
    ];

    subscribe((event) => firstSubscriberEvents.push(event));
    subscribe((event) => secondSubscriberEvents.push(event));

    for (const event of events) {
      publishUiEvent(event);
    }

    expect(firstSubscriberEvents).toEqual(events);
    expect(secondSubscriberEvents).toEqual(events);
  });

  it('stops delivering future events to an unsubscribed listener', () => {
    const activeSubscriberEvents = [];
    const unsubscribedSubscriberEvents = [];
    const unsubscribe = subscribe((event) => unsubscribedSubscriberEvents.push(event));
    subscribe((event) => activeSubscriberEvents.push(event));

    publishUiEvent({ id: 'ui-1', type: 'chat.response.delta' });
    unsubscribe();
    publishUiEvent({ id: 'ui-2', type: 'chat.response.completed' });

    expect(unsubscribedSubscriberEvents).toEqual([
      { id: 'ui-1', type: 'chat.response.delta' },
    ]);
    expect(activeSubscriberEvents).toEqual([
      { id: 'ui-1', type: 'chat.response.delta' },
      { id: 'ui-2', type: 'chat.response.completed' },
    ]);
  });

  it('continues the current delivery when another listener unsubscribes during it', () => {
    const removedSubscriberEvents = [];
    const remainingSubscriberEvents = [];
    let unsubscribeRemovedSubscriber;

    subscribe(() => unsubscribeRemovedSubscriber());
    unsubscribeRemovedSubscriber = subscribe((event) => removedSubscriberEvents.push(event));
    subscribe((event) => remainingSubscriberEvents.push(event));

    publishUiEvent({ id: 'ui-1', type: 'chat.response.delta' });
    publishUiEvent({ id: 'ui-2', type: 'chat.response.completed' });

    expect(removedSubscriberEvents).toEqual([
      { id: 'ui-1', type: 'chat.response.delta' },
    ]);
    expect(remainingSubscriberEvents).toEqual([
      { id: 'ui-1', type: 'chat.response.delta' },
      { id: 'ui-2', type: 'chat.response.completed' },
    ]);
  });
});
