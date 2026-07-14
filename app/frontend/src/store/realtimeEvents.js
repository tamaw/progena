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
  for (const listener of [...subscribers]) {
    listener(event);
  }
}
