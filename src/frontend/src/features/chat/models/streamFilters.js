import { isToolEventFailure } from './toolFailureModel';
import { A2A_PRIMARY_TOOL_NAME, isA2AToolName } from './a2aTools';

export const STREAM_EVENT_CATEGORIES = [
  { id: 'tools', label: 'Tools', description: 'Tool calls and results', color: 'var(--color-success)' },
  { id: 'plan', label: 'Plans', description: 'Plan updates', color: 'var(--color-accent)' },
  { id: 'reasoning', label: 'Reasoning', description: 'Reasoning summaries and details', color: 'var(--color-accent-secondary)' },
  { id: 'delegations', label: 'Delegations', description: 'Subagents and A2A calls', color: 'var(--color-success)' },
  { id: 'errors', label: 'Errors', description: 'Failed tools and delegations', color: 'var(--color-error)' },
  { id: 'debug', label: 'Debug', description: 'Graph steps and diagnostic events', color: 'var(--color-text-muted)', defaultEnabled: false },
];

export const PERSISTED_CATEGORIES = new Set(['tools', 'plan', 'reasoning', 'subagents', 'a2a']);

const DEFAULT_FILTERS = STREAM_EVENT_CATEGORIES.reduce((acc, c) => {
  acc[c.id] = c.defaultEnabled !== false;
  return acc;
}, {});

export function createDefaultStreamFilters() {
  return { ...DEFAULT_FILTERS };
}

// `metadata` is optional -- callers that only have a bare eventType (or a
// synthetic sentinel event, see Message.jsx's card-level gates) can omit it.
// A2A tools reuse generic tool lifecycle events, so distinguishing their
// category takes checking metadata.tool_name. Check ahead of generic tool
// errors so A2A failures remain in the A2A bucket.
export function categoryForEventType(eventType, metadata) {
  if (typeof eventType !== 'string') return 'debug';
  if (eventType === 'message_delta') return null;
  if (eventType === 'assistant_metadata') return null;
  if (eventType === 'node_update') return 'debug';
  if (eventType === 'reasoning_delta' || eventType === 'reasoning_summary') return 'reasoning';
  if (eventType === 'a2a_call_progress') return 'a2a';
  if (isA2AToolName(metadata?.tool_name)) return 'a2a';
  if (eventType === 'tool_error') return 'errors';
  if (eventType.startsWith('tool_')) return 'tools';
  if (eventType === 'todos_update') return 'plan';
  if (eventType.startsWith('async_subagent_')) return 'subagents';
  if (eventType.startsWith('subagent_')) return 'subagents';
  return 'debug';
}

// Storage/reconciliation still use the original event categories. Only the
// presentation controls combine delegation types and put errors with tools.
function activityCategory(category) {
  if (category === 'a2a' || category === 'subagents') return 'delegations';
  return category === 'errors' ? 'tools' : category;
}

export function isEventVisible(event, filters) {
  if (!event) return false;
  const category = activityCategory(categoryForEventType(event.eventType, event.metadata));
  if (!category) return false;
  if (isToolEventFailure(event)) return filters?.errors !== false;
  return filters?.[category] ?? DEFAULT_FILTERS[category];
}

function toolCallId(event) {
  const callId = event?.metadata?.tool_call_id;
  return typeof callId === 'string' && callId ? callId : null;
}

function turnEventFilter(events, filter) {
  if (typeof filter !== 'function') return () => true;
  const failuresByCall = new Map();
  for (const event of events) {
    const callId = toolCallId(event);
    if (callId && isToolEventFailure(event)) failuresByCall.set(callId, event);
  }
  return (event) => {
    const failure = failuresByCall.get(toolCallId(event));
    return filter(failure || event);
  };
}

export function filterStreamEvents(events, filter) {
  return typeof filter === 'function' ? events.filter(turnEventFilter(events, filter)) : events;
}

/** Filter whole calls/cards, never conversation answers or attached images. */
export function filterTimelineItems(items, filter) {
  if (typeof filter !== 'function') return items;
  const events = [];
  for (const item of items) {
    if (item.kind === 'stream_event') events.push(item.event);
  }
  const filterEvent = turnEventFilter(events, filter);
  return items.filter((item) => {
    if (item.kind === 'stream_event') return filterEvent(item.event);
    if (item.kind !== 'subagent' && item.kind !== 'async_subagent' && item.kind !== 'a2a_call') return true;
    const failed = item.status === 'error' || Boolean(item.error)
      || item.entries?.some((entry) => entry.status === 'error')
      || item.messages?.some((entry) => entry.state === 'failed' || entry.state === 'rejected');
    return filter({
      eventType: item.kind === 'subagent' ? 'subagent_start' : (
        item.kind === 'async_subagent' ? 'async_subagent_start' : 'tool_start'
      ),
      metadata: {
        tool_name: item.kind === 'a2a_call' ? A2A_PRIMARY_TOOL_NAME : 'task',
        success: !failed,
      },
    });
  });
}

// Continuation events share their opening event's category -- counting them
// too would double- (or many-times-, e.g. subagent_message_delta fires once
// per streamed token) count a single tool call or subagent dispatch as if it
// were several. Only the event that opens each one should increment its
// category's tally.
const CONTINUATION_EVENT_TYPES = new Set([
  'tool_result',
  'subagent_result',
  'subagent_tool_start',
  'subagent_tool_result',
  'subagent_tool_error',
  'subagent_message_delta',
  'async_subagent_update',
  'async_subagent_pause',
  'async_subagent_result',
  'async_subagent_cancel',
  'tool_error',
  'a2a_call_progress',
]);

/**
 * Tallies one message's activity, one increment per invocation rather than
 * per raw event. Reasoning is counted separately from the rendered summaries.
 */
export function countCategorizedEvents(streamEvents, counts) {
  for (const event of (Array.isArray(streamEvents) ? streamEvents : [])) {
    const cat = activityCategory(categoryForEventType(event?.eventType, event?.metadata));
    if (!cat || cat === 'reasoning' || cat === 'debug') continue;
    if (CONTINUATION_EVENT_TYPES.has(event.eventType)) continue;
    if (cat in counts) counts[cat] += 1;
  }
  return counts;
}

export function loadStreamFilters(storageKey) {
  try {
    const raw = localStorage.getItem(storageKey);
    if (!raw) return createDefaultStreamFilters();
    const parsed = JSON.parse(raw);
    const filters = createDefaultStreamFilters();
    for (const key of Object.keys(filters)) {
      if (typeof parsed?.[key] === 'boolean') filters[key] = parsed[key];
    }
    // Preserve either previously visible delegation type when combining them.
    if (typeof parsed?.delegations !== 'boolean') {
      filters.delegations = parsed?.subagents !== false || parsed?.a2a !== false;
    }
    return filters;
  } catch {
    return createDefaultStreamFilters();
  }
}
