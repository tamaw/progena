// App-owned browser event and localStorage contracts. Keep persisted values and
// event names stable: they are observable across a page's independently mounted
// frontend surfaces.
export const BROWSER_EVENT = Object.freeze({
  WORKSPACE_SWITCHED: 'progena:workspace-switched',
  OPEN_QUICK_CHAT: 'progena:open-quick-chat',
  QUICK_CHAT_INLINE_STATUS: 'progena:quick-chat-inline-status',
  LOAD_CHAT_HISTORY: 'progena:load-chat-history',
});

export const QUICK_CHAT_MODE = Object.freeze({
  SCHEDULE_CREATE: 'schedule-create', MCP_CREATE: 'mcp-create', TASK_CREATE: 'task-create',
  KNOWLEDGE_CREATE: 'knowledge-create', PROFILE_CREATE: 'profile-create', REPOSITORY_CREATE: 'repository-create',
  INBOX_COMPOSE: 'inbox-compose', INBOX_EDIT: 'inbox-edit', WORKFLOW_CREATE: 'workflow-create',
  ROUTINE_CREATE: 'routine-create', INSTRUCTION_CREATE: 'instruction-create',
});

export function dispatchBrowserEvent(name, detail) {
  window.dispatchEvent(new CustomEvent(name, detail === undefined ? undefined : { detail }));
}
export function dispatchQuickChatOpen(detail) { dispatchBrowserEvent(BROWSER_EVENT.OPEN_QUICK_CHAT, detail); }
export function dispatchQuickChatInlineStatus(detail) { dispatchBrowserEvent(BROWSER_EVENT.QUICK_CHAT_INLINE_STATUS, detail); }
export function dispatchWorkspaceSwitched() { dispatchBrowserEvent(BROWSER_EVENT.WORKSPACE_SWITCHED); }
export function dispatchChatHistoryLoad(detail) { dispatchBrowserEvent(BROWSER_EVENT.LOAD_CHAT_HISTORY, detail); }

export const BROWSER_STORAGE_KEY = Object.freeze({
  THEME: 'progena_theme', CHAT_HISTORY: 'progena_chat_history', SETTINGS: 'progena_settings',
  INPUT_HISTORY: 'progena_input_history', SIDEBAR_COLLAPSED: 'progena_sidebar_collapsed',
  PINNED_PANEL: 'progena_pinned_panel', STREAM_FILTERS: 'progena_stream_filters',
  QUICK_CHAT_HANDOFF: 'progena_quick_chat_handoff', IDLE_TIMEOUT_MINUTES: 'progena_idle_timeout_minutes',
  DENSITY: 'progena_density', ARCHIVED_CHAT_SESSIONS: 'progena:archived-chat-sessions',
  PINNED_CHAT_SESSIONS: 'progena:pinned-chat-sessions', SOLUTIONS: 'progena:solutions',
  WORKSPACE_REGISTRY: 'progena_workspaces', DEFAULT_WORKSPACE: 'progena_default_workspace',
});

export const WORKSPACE_LOCAL_STORAGE_KEYS = Object.freeze([
  BROWSER_STORAGE_KEY.ARCHIVED_CHAT_SESSIONS, BROWSER_STORAGE_KEY.PINNED_CHAT_SESSIONS,
  BROWSER_STORAGE_KEY.SOLUTIONS, BROWSER_STORAGE_KEY.CHAT_HISTORY, BROWSER_STORAGE_KEY.QUICK_CHAT_HANDOFF,
  BROWSER_STORAGE_KEY.INPUT_HISTORY, BROWSER_STORAGE_KEY.STREAM_FILTERS,
]);
export const GLOBAL_PREFERENCE_STORAGE_KEYS = Object.freeze([
  BROWSER_STORAGE_KEY.THEME, BROWSER_STORAGE_KEY.SETTINGS, BROWSER_STORAGE_KEY.SIDEBAR_COLLAPSED,
  BROWSER_STORAGE_KEY.PINNED_PANEL, BROWSER_STORAGE_KEY.IDLE_TIMEOUT_MINUTES, BROWSER_STORAGE_KEY.DENSITY,
]);
