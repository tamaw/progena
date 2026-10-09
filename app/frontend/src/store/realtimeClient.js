import { createUuid } from '../lib/uuid';

// One identifier per tab, shared by its canonical stream and finite requests.
export const realtimeClientId = createUuid();
let ready = false;
const waiters = new Set();

export function setRealtimeClientReady(value) {
  ready = value;
  if (ready) {
    for (const resolve of waiters) resolve();
    waiters.clear();
  }
}

export function waitForRealtimeClient() {
  return ready ? Promise.resolve() : new Promise((resolve) => waiters.add(resolve));
}
