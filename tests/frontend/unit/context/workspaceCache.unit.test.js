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
  'progenitor_chat_history',
  'progenitor_quick_chat_handoff',
  'progenitor_input_history',
  'progenitor_stream_filters',
];

// Preferences that must survive a workspace switch -- they describe the
// browser/UI, not data belonging to a particular backend.
const PRESERVED_KEYS = [
  'progenitor_theme',
  'progenitor_notifications',
  'progenitor_sounds',
  'progenitor_sidebar_collapsed',
  'progenitor_workspaces',
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
