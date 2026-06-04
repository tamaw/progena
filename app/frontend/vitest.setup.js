import { beforeEach } from 'vitest';

// Minimal in-memory localStorage so chatStorage/streamFilters can run under the
// Node test environment without pulling in jsdom.
class MemoryStorage {
  constructor() {
    this.store = new Map();
  }

  getItem(key) {
    return this.store.has(key) ? this.store.get(key) : null;
  }

  setItem(key, value) {
    this.store.set(key, String(value));
  }

  removeItem(key) {
    this.store.delete(key);
  }

  clear() {
    this.store.clear();
  }
}

// Define unconditionally (overriding Node's experimental localStorage, which
// warns unless --localstorage-file is set) so storage is deterministic.
Object.defineProperty(globalThis, 'localStorage', {
  configurable: true,
  writable: true,
  value: new MemoryStorage(),
});

beforeEach(() => {
  globalThis.localStorage.clear();
});
