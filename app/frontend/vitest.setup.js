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

// Bare-existence document stub, NOT a functional DOM. It only satisfies
// `typeof document !== 'undefined'` guards that run at module-eval time in
// the Node test environment; its createElement() result has no working
// cloneNode/appendChild. sanitizeHtmlFragment()/appendStreamingCursor() in
// messageRenderUtils.js need real node behavior (template.content.cloneNode,
// div.appendChild, ...), so any test exercising those paths -- e.g.
// Message.unit.test.jsx -- must install its own richer document mock in
// beforeAll/afterAll rather than relying on this fallback.
if (typeof globalThis.document === 'undefined') {
  const mockTemplate = {
    innerHTML: '',
    content: {
      childNodes: [],
      querySelectorAll: () => [],
      removeChild: () => {},
      appendChild: () => {},
    },
  };
  globalThis.document = {
    createElement: (tag) => {
      if (tag === 'template') return { ...mockTemplate };
      return { ...mockTemplate };
    },
    createTreeWalker: () => ({
      nextNode: () => null,
      currentNode: null,
    }),
  };
}
