export const STREAM_EVENT_CATEGORIES = [
  { id: 'tools', label: 'Tools', description: 'tool_start / tool_result events', color: 'var(--color-success)' },
  { id: 'reasoning', label: 'Reasoning', description: 'reasoning deltas / summaries', color: '#a78bfa' },
  { id: 'user', label: 'User', description: 'user messages', color: '#60a5fa' },
  { id: 'agent', label: 'Agent', description: 'agent responses', color: '#8b5cf6' },
  { id: 'errors', label: 'Errors', description: 'tool errors and failed responses', color: 'var(--color-error)' },
  { id: 'images', label: 'Images', description: 'image attachments', color: '#f59e0b' },
  { id: 'debug', label: 'Debug', description: 'graph node steps and misc events', color: 'var(--color-text-muted)' },
];

export const PERSISTED_CATEGORIES = new Set(['tools', 'reasoning']);

const DEFAULT_FILTERS = STREAM_EVENT_CATEGORIES.reduce((acc, c) => {
  acc[c.id] = true;
  return acc;
}, {});

export function categoryForEventType(eventType) {
  if (typeof eventType !== 'string') return 'debug';
  if (eventType === 'message_delta') return null;
  if (eventType === 'node_update') return 'debug';
  if (eventType === 'reasoning_delta' || eventType === 'reasoning_summary') return 'reasoning';
  if (eventType === 'tool_error') return 'errors';
  if (eventType.startsWith('tool_')) return 'tools';
  return 'debug';
}

export function isEventVisible(event, filters) {
  if (!event) return false;
  const category = categoryForEventType(event.eventType);
  if (!category) return false;
  return filters?.[category] !== false;
}

export function loadStreamFilters(storageKey) {
  try {
    const raw = localStorage.getItem(storageKey);
    if (!raw) return { ...DEFAULT_FILTERS };
    const parsed = JSON.parse(raw);
    return STREAM_EVENT_CATEGORIES.reduce((acc, c) => {
      acc[c.id] = parsed && typeof parsed[c.id] === 'boolean' ? parsed[c.id] : true;
      return acc;
    }, {});
  } catch {
    return { ...DEFAULT_FILTERS };
  }
}
