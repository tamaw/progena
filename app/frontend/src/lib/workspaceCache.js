import {
  ARCHIVED_CHAT_SESSIONS_KEY,
  CUSTOM_CHAT_TITLE_KEY,
  PINNED_CHAT_SESSIONS_KEY,
  SHARED_CHAT_SESSIONS_KEY,
} from '../components/chat/chatStorage';
import { SOLUTIONS_STORAGE_KEY } from '../data/solutions';

// Browser-origin caches that hold data scoped to whichever backend was
// active when they were written -- chat drafts/history, session lists,
// custom titles, solutions, quick-chat handoff. localStorage is scoped to
// the *frontend's* origin, not the backend workspace, so these leak across
// an in-place workspace switch unless explicitly cleared.
//
// Deliberately NOT included: theme, notifications, sounds, sidebar-collapsed,
// and the workspace registry itself -- those are browser/UI preferences, not
// per-backend data, and should survive a switch.
const WORKSPACE_SCOPED_STORAGE_KEYS = [
  ARCHIVED_CHAT_SESSIONS_KEY,
  CUSTOM_CHAT_TITLE_KEY,
  PINNED_CHAT_SESSIONS_KEY,
  SHARED_CHAT_SESSIONS_KEY,
  SOLUTIONS_STORAGE_KEY,
  'progenitor_chat_history',
  'progenitor_quick_chat_handoff',
  'progenitor_input_history',
  'progenitor_stream_filters',
];

/** Clears every localStorage cache scoped to the previously-active workspace. */
export function clearWorkspaceScopedCaches() {
  WORKSPACE_SCOPED_STORAGE_KEYS.forEach((key) => localStorage.removeItem(key));
}
