import { describe, it, expect, beforeEach } from 'vitest';
import { clearWorkspaceScopedCaches } from '@frontend/context/workspaceCache';
import {
  BROWSER_STORAGE_KEY,
  GLOBAL_PREFERENCE_STORAGE_KEYS,
  WORKSPACE_LOCAL_STORAGE_KEYS,
} from '@frontend/lib/browserContracts';

const WORKSPACE_SCOPED_KEYS = WORKSPACE_LOCAL_STORAGE_KEYS;

// Preferences and workspace routing state must survive a workspace switch --
// they describe the browser rather than data belonging to a particular backend.
const PRESERVED_KEYS = [
  ...GLOBAL_PREFERENCE_STORAGE_KEYS,
  BROWSER_STORAGE_KEY.WORKSPACE_REGISTRY,
  BROWSER_STORAGE_KEY.DEFAULT_WORKSPACE,
];

beforeEach(() => {
  localStorage.clear();
  [...WORKSPACE_SCOPED_KEYS, ...PRESERVED_KEYS].forEach((key) => localStorage.setItem(key, 'x'));
});

describe('clearWorkspaceScopedCaches', () => {
  it('removes every workspace-scoped cache key', () => {
    clearWorkspaceScopedCaches();
    WORKSPACE_SCOPED_KEYS.forEach((key) => expect(localStorage.getItem(key)).toBeNull());
  });

  it('leaves browser/UI preference keys untouched', () => {
    clearWorkspaceScopedCaches();
    PRESERVED_KEYS.forEach((key) => expect(localStorage.getItem(key)).toBe('x'));
  });
});
