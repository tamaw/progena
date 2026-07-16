// Ordered in-process fan-out for ephemeral ui.event consumers. Durable event
// state remains in Redux; foreground consumers subscribe here so a high-rate
// response stream does not redraw every component that watches Redux history.
const subscribers = new Set();

export function subscribeUiEvent(listener) {
  subscribers.add(listener);
  return () => {
    subscribers.delete(listener);
  };
}

export function publishUiEvent(event) {
  // Snapshot the set so subscribers can safely unsubscribe while handling an event.
  for (const listener of [...subscribers]) {
    try {
      listener(event);
    } catch {
      // One consumer must not prevent the remaining subscribers from receiving the event.
    }
  }
}
