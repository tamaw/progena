import { describe, it, expect, beforeEach } from 'vitest';
import { clearWorkspaceScopedCaches } from '@frontend/context/workspaceCache';
import {
  ARCHIVED_CHAT_SESSIONS_KEY,
  PINNED_CHAT_SESSIONS_KEY,
} from '@frontend/features/chat/models/chatStorage';
import { SOLUTIONS_STORAGE_KEY } from '@frontend/features/projects/solutions';

const WORKSPACE_SCOPED_KEYS = [
  ARCHIVED_CHAT_SESSIONS_KEY,
  PINNED_CHAT_SESSIONS_KEY,
  SOLUTIONS_STORAGE_KEY,
  'progena_chat_history',
  'progena_quick_chat_handoff',
  'progena_input_history',
  'progena_stream_filters',
];

// Preferences that must survive a workspace switch -- they describe the
// browser/UI, not data belonging to a particular backend.
const PRESERVED_KEYS = [
  'progena_theme',
  'progena_notifications',
  'progena_sounds',
  'progena_sidebar_collapsed',
  'progena_workspaces',
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
