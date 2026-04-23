export const STREAM_EVENT_CATEGORIES = [
  { id: 'tools',  label: 'Tools',  description: 'tool_start / tool_result / tool_error' },
  { id: 'steps',  label: 'Steps',  description: 'graph node updates' },
  { id: 'reasoning', label: 'Reasoning', description: 'reasoning deltas / summaries' },
  { id: 'tokens', label: 'Tokens', description: 'streaming text deltas' },
  { id: 'debug',  label: 'Debug',  description: 'misc / unknown events' },
];

export const PERSISTED_CATEGORIES = new Set(['tools', 'steps', 'reasoning']);

const DEFAULT_FILTERS = STREAM_EVENT_CATEGORIES.reduce((acc, c) => {
  acc[c.id] = true;
  return acc;
}, {});

export function categoryForEventType(eventType) {
  if (typeof eventType !== 'string') return 'debug';
  if (eventType === 'message_delta') return 'tokens';
  if (eventType === 'node_update') return 'steps';
  if (eventType === 'reasoning_delta' || eventType === 'reasoning_summary') return 'reasoning';
  if (eventType.startsWith('tool_')) return 'tools';
  return 'debug';
}

export function isEventVisible(event, filters) {
  if (!event) return false;
  const category = categoryForEventType(event.eventType);
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
