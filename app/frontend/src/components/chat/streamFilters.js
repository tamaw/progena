export const STREAM_EVENT_CATEGORIES = [
  { id: 'tools', label: 'Tools', description: 'tool_start / tool_result events', color: 'var(--color-success)' },
  { id: 'plan', label: 'Plan', description: 'write_todos plan updates', color: 'var(--color-accent)' },
  { id: 'reasoning', label: 'Reasoning', description: 'reasoning deltas / summaries', color: '#a78bfa' },
  { id: 'subagents', label: 'Subagents', description: 'Nested subagent execution (task tool delegation)', color: 'var(--color-success)' },
  { id: 'a2a', label: 'A2A', description: 'call_agent invocations of deployed A2A agents', color: 'var(--color-a2a)' },
  { id: 'user', label: 'User', description: 'user messages', color: '#60a5fa' },
  { id: 'agent', label: 'Agent', description: 'agent responses', color: '#8b5cf6' },
  { id: 'errors', label: 'Errors', description: 'tool errors and failed responses', color: 'var(--color-error)' },
  { id: 'images', label: 'Images', description: 'image attachments', color: '#f59e0b' },
  { id: 'debug', label: 'Debug', description: 'graph node steps and misc events', color: 'var(--color-text-muted)' },
];

export const PERSISTED_CATEGORIES = new Set(['tools', 'plan', 'reasoning', 'subagents', 'a2a']);

const DEFAULT_FILTERS = STREAM_EVENT_CATEGORIES.reduce((acc, c) => {
  acc[c.id] = true;
  return acc;
}, {});

// `metadata` is optional -- callers that only have a bare eventType (or a
// synthetic sentinel event, see Message.jsx's card-level gates) can omit it,
// but call_agent can only be told apart from every other tool this way: it
// deliberately reuses the generic tool_start/tool_result/tool_error events
// rather than getting its own dedicated types (only the intermediate
// a2a_call_progress is dedicated -- see A2ACallCard.jsx), so distinguishing
// its category takes checking metadata.tool_name, not just the event type
// string. Checked ahead of the generic tool_error/tool_ prefix checks so
// call_agent's own errors land in 'a2a' rather than the catch-all 'errors'
// bucket -- the same precedent subagent_tool_error already sets by staying
// under 'subagents' instead.
export function categoryForEventType(eventType, metadata) {
  if (typeof eventType !== 'string') return 'debug';
  if (eventType === 'message_delta') return null;
  if (eventType === 'assistant_metadata') return null;
  if (eventType === 'node_update') return 'debug';
  if (eventType === 'reasoning_delta' || eventType === 'reasoning_summary') return 'reasoning';
  if (eventType === 'a2a_call_progress') return 'a2a';
  if (metadata?.tool_name === 'call_agent') return 'a2a';
  if (eventType === 'tool_error') return 'errors';
  if (eventType.startsWith('tool_')) return 'tools';
  if (eventType === 'todos_update') return 'plan';
  if (eventType.startsWith('subagent_')) return 'subagents';
  return 'debug';
}

export function isEventVisible(event, filters) {
  if (!event) return false;
  const category = categoryForEventType(event.eventType, event.metadata);
  if (!category) return false;
  return filters?.[category] !== false;
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
  'a2a_call_progress',
]);

/**
 * Tallies one message's stream events into `counts` (mutated in place), one
 * increment per invocation rather than per raw event -- so the filter chips
 * agree with conversationModel.js's countToolInvocations(), which counts
 * only tool_start for the page header's "N tools" figure. `tool_error` is
 * the one exception among continuation types: it only shares a category
 * with its own tool_start when the call was call_agent ('a2a', already
 * counted there via that tool_start); for every other tool, tool_error is
 * the sole signal that invocation failed, so it still increments 'errors'.
 */
export function countCategorizedEvents(streamEvents, counts) {
  for (const event of (Array.isArray(streamEvents) ? streamEvents : [])) {
    const cat = categoryForEventType(event?.eventType, event?.metadata);
    if (!cat || cat === 'reasoning') continue;
    if (CONTINUATION_EVENT_TYPES.has(event.eventType)) continue;
    if (event.eventType === 'tool_error' && cat === 'a2a') continue;
    if (cat in counts) counts[cat] += 1;
  }
  return counts;
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
