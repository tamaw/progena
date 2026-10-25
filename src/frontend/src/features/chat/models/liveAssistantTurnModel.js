/**
 * Pure model for an in-flight assistant turn (live turn).
 *
 * A live assistant turn holds everything the frontend knows about an ongoing
 * assistant response before it is finalised and pushed into the committed
 * `messages` array.  State transitions are driven by stream events arriving
 * from the backend SSE stream:
 *
 *   1. `assistant_metadata` → updates display metadata only.
 *   2. First visible stream event (`tool_start`, `tool_result`, `node_update`,
 *      reasoning events) → appends a stream_event timeline item but does NOT
 *      create the assistant text row.
 *   3. First text chunk → materialises the assistant text row (assigns
 *      `assistantMessageId`, stamps `assistantTimestamp = now`).
 *   4. Later chunks → update only that assistant row's content/tokenCount.
 *   5. Completion / error → finalises the turn into a committed message.
 *
 * Kept as a separate module so the state machine can be unit tested without
 * rendering a React hook.
 */

import { createStreamEvent, appendStreamEvent, appendReasoningText, extractReasoningTokenCount, extractCacheReadTokenCount, mergeReasoningEvents, isReasoningEvent, isRenderableStreamEvent, MAX_STREAM_EVENTS_PER_MESSAGE } from './streamEventsModel';
import { isA2AToolName } from './a2aTools';
import { normalizeContextUsage } from './contextWindowModel';
import { isToolEventFailure } from './toolFailureModel';

/** Create a fresh live assistant turn with no visible output yet. */
export function createLiveAssistantTurn({ streamId, startedAt }) {
  return {
    streamId,
    startedAt,
    assistantMessageId: null,
    assistantTimestamp: null,
    content: '',
    tokenCount: 0,
    reasoningText: '',
    reasoningTokenCount: 0,
    cacheReadTokenCount: 0,
    contextUsage: null,
    reasoningSummaryReceived: false,
    streamEvents: [],
    displayAgentName: '',
    displayModelName: '',
    displayToolCalls: [],
    timelineItems: [],
    // 'work' while the latest visible output is a tool/reasoning/plan row,
    // 'text' once answer text is streaming -- drives the live work preview.
    lastActivity: null,
    // Answer text streamed since the last work row. If a tool call follows,
    // it was the agent narrating that call, not answering: it moves from the
    // answer onto the call as its commentary.
    pendingCommentary: '',
    isStreaming: true,
    error: false,
  };
}

/** Apply display metadata from an `assistant_metadata` control event. */
export function applyAssistantMetadata(turn, metadata) {
  const next = { ...turn };
  if (typeof metadata.display_agent_name === 'string' && metadata.display_agent_name) {
    next.displayAgentName = metadata.display_agent_name;
  }
  if (typeof metadata.display_model_name === 'string' && metadata.display_model_name) {
    next.displayModelName = metadata.display_model_name;
  }
  if (Array.isArray(metadata.display_tool_calls)) {
    next.displayToolCalls = metadata.display_tool_calls;
  }
  return next;
}

// Streamed subagent deltas are independently meaningful, even when two
// adjacent payloads happen to have identical content and metadata. The shared
// append helper intentionally coalesces value-identical events for ordinary
// stream rows, so bypass that value-based deduplication for this event type
// while retaining the same bounded history semantics.
function appendLiveStreamEvent(events, nextEvent) {
  if (nextEvent?.eventType !== 'subagent_message_delta') {
    return appendStreamEvent(events, nextEvent);
  }
  const currentEvents = Array.isArray(events) ? events : [];
  const next = currentEvents.concat(nextEvent);
  const overflow = Math.max(0, next.length - MAX_STREAM_EVENTS_PER_MESSAGE);
  return overflow > 0 ? next.slice(overflow) : next;
}
// Subagent execution events (nested `task` tool delegation). Unlike the
// reasoning/assistant-message rows below (at most one per turn), the agent
// can delegate to several subagents in parallel within one turn, so these
// are keyed by the dispatching tool_call_id and each gets its own
// independently-updatable card -- see applySubagentEvent.
const SUBAGENT_EVENT_TYPES = new Set([
  'subagent_start',
  'subagent_result',
  'subagent_message_delta',
  'subagent_tool_start',
  'subagent_tool_result',
  'subagent_tool_error',
]);

function subagentItemId(toolCallId) {
  return `subagent-${toolCallId}`;
}

export const TASK_SUMMARY_MAX_LENGTH = 140;

/** Normalize a task description into the bounded summary shown on subagent cards. */
export function normalizeTaskSummary(value) {
  if (typeof value !== 'string') return '';
  const normalized = value.replace(/\s+/g, ' ').trim();
  if (!normalized) return '';
  return normalized.length > TASK_SUMMARY_MAX_LENGTH
    ? `${normalized.slice(0, TASK_SUMMARY_MAX_LENGTH - 1)}…`
    : normalized;
}

function taskSummaryFromMetadata(metadata) {
  return normalizeTaskSummary(metadata?.task_summary);
}

function addTaskSummaryMetadata(event) {
  if (!event || (event.eventType !== 'subagent_start' && event.eventType !== 'subagent_result')) {
    return event;
  }
  const metadata = event.metadata || {};
  const taskSummary = taskSummaryFromMetadata(metadata);
  if (!taskSummary || metadata.task_summary === taskSummary) return event;
  return { ...event, metadata: { ...metadata, task_summary: taskSummary } };
}

function createSubagentItem(toolCallId, subagentName, startedAt) {
  return {
    kind: 'subagent',
    taskSummary: '',
    id: subagentItemId(toolCallId),
    toolCallId,
    subagentName: subagentName || '',
    status: 'running',
    startedAt,
    finishedAt: null,
    durationMs: null,
    resultPreview: '',
    error: null,
    // Single ordered chain of the subagent's own output, mirroring how
    // A2ACallCard renders `item.messages` as a chain of A2ASubEntry nodes:
    // each entry is either a `{ kind: 'message', ... }` block of streamed
    // text or a `{ kind: 'tool', ... }` nested tool call, in true arrival
    // order. A tool call closes out whatever message entry preceded it, so
    // the next delta opens a fresh entry -- text either side of a nested
    // tool call renders as two distinct cards instead of one blob with the
    // tool calls glued on underneath (the old `content` + `children` split).
    entries: [],
  };
}

// Extends the trailing entry if it's still an open text block, otherwise
// opens a new one. `upsertSubagentToolEntry` below never extends an
// existing message entry, so any nested tool call ends the current run and
// the next delta always starts a fresh message entry.
function appendSubagentMessageDelta(entries, deltaText) {
  const last = entries.length > 0 ? entries[entries.length - 1] : null;
  if (last && last.kind === 'message') {
    return [...entries.slice(0, -1), { ...last, text: last.text + deltaText }];
  }
  return [...entries, { kind: 'message', id: `msg-${entries.length}`, text: deltaText }];
}

// Find-or-create-or-update a nested tool call entry within the same
// unified chain, keyed by the nested tool's own call id (distinct from the
// subagent card's own toolCallId, which is the *parent* task call).
function upsertSubagentToolEntry(entries, nestedToolCallId, patch) {
  if (!nestedToolCallId) return entries;
  const index = entries.findIndex((entry) => entry.kind === 'tool' && entry.id === nestedToolCallId);
  if (index === -1) {
    return [
      ...entries,
      {
        kind: 'tool',
        id: nestedToolCallId,
        toolName: '',
        status: 'running',
        argsPreview: null,
        resultPreview: '',
        error: null,
        durationMs: null,
        ...patch,
      },
    ];
  }
  return entries.map((entry, idx) => (idx === index ? { ...entry, ...patch } : entry));
}

function isTerminalLifecycleStatus(status) {
  return status === 'success' || status === 'error';
}

function hasVisibleTimelineOutput(timelineItems) {
  return (Array.isArray(timelineItems) ? timelineItems : []).some((item) => {
    if (!item || typeof item !== 'object') {
      return false;
    }
    if (item.kind === 'assistant_message') {
      return typeof item.content === 'string' && item.content.trim().length > 0;
    }
    if (item.kind === 'stream_event') {
      return isRenderableStreamEvent(item.event);
    }
    return item.kind === 'subagent' || item.kind === 'a2a_call';
  });
}

function applySubagentEventToItem(item, nextEvent) {
  if (isTerminalLifecycleStatus(item.status)) {
    return item;
  }

  const metadata = nextEvent.metadata || {};
  const durationMs = Number.isFinite(metadata.duration_ms) ? metadata.duration_ms : null;
  const taskSummary = taskSummaryFromMetadata(metadata);
  switch (nextEvent.eventType) {

    case 'subagent_start':
      return {
        ...item,
        taskSummary: taskSummary || item.taskSummary || '',
        subagentName: metadata.subagent_name || item.subagentName,
        status: 'running',
      };

    case 'subagent_message_delta':
      return { ...item, entries: appendSubagentMessageDelta(item.entries, nextEvent.content || '') };

    case 'subagent_tool_start':
      return {
        ...item,
        subagentName: metadata.subagent_name || item.subagentName,
        entries: upsertSubagentToolEntry(item.entries, metadata.nested_tool_call_id, {
          toolName: metadata.nested_tool_name || '',
          status: 'running',
          argsPreview: metadata.args_preview ?? null,
        }),
      };

    case 'subagent_tool_result':
      return {
        ...item,
        entries: upsertSubagentToolEntry(item.entries, metadata.nested_tool_call_id, {
          toolName: metadata.nested_tool_name || '',
          // Returned failures (non-zero exit, thrown JS) arrive as results.
          status: isToolEventFailure({
            eventType: 'tool_result',
            metadata: { ...metadata, tool_name: metadata.nested_tool_name },
          }) ? 'error' : 'success',
          resultPreview: typeof metadata.result_preview === 'string' ? metadata.result_preview : '',
          durationMs,
        }),
      };

    case 'subagent_tool_error':
      return {
        ...item,
        entries: upsertSubagentToolEntry(item.entries, metadata.nested_tool_call_id, {
          toolName: metadata.nested_tool_name || '',
          status: 'error',
          error: typeof metadata.error === 'string' ? metadata.error : '',
          durationMs,
        }),
      };

    case 'subagent_result':
      return {
        ...item,
        taskSummary: taskSummary || item.taskSummary || '',
        subagentName: metadata.subagent_name || item.subagentName,
        status: metadata.success === false ? 'error' : 'success',
        finishedAt: nextEvent.timestamp ?? item.finishedAt,
        durationMs: durationMs ?? item.durationMs,
        resultPreview: typeof metadata.result_preview === 'string' ? metadata.result_preview : item.resultPreview,
        error: metadata.success === false ? (metadata.error || '') : item.error,
      };

    default:
      return item;
  }
}

/**
 * Find-or-create-or-update the subagent card for a `subagent_*` stream
 * event, keyed strictly by the event's own `tool_call_id` so concurrent
 * subagents' cards never touch each other regardless of arrival
 * interleaving. Returns `null` (not an unchanged array) when `nextEvent`
 * isn't a subagent event or carries no `tool_call_id`, so callers can fall
 * through to their own generic handling.
 */
export function applySubagentEvent(timelineItems, nextEvent) {
  const normalizedEvent = addTaskSummaryMetadata(nextEvent);
  if (!normalizedEvent || !SUBAGENT_EVENT_TYPES.has(normalizedEvent.eventType)) return null;
  const toolCallId = normalizedEvent.metadata?.tool_call_id;
  if (!toolCallId) return null;

  const itemId = subagentItemId(toolCallId);
  const index = timelineItems.findIndex((entry) => entry.kind === 'subagent' && entry.id === itemId);

  if (index === -1) {
    const seed = createSubagentItem(toolCallId, normalizedEvent.metadata?.subagent_name, normalizedEvent.timestamp);
    return [...timelineItems, applySubagentEventToItem(seed, normalizedEvent)];
  }

  return timelineItems.map((entry, idx) => (
    idx === index ? applySubagentEventToItem(entry, normalizedEvent) : entry
  ));
}

// A2A tool progress cards. Unlike subagents (dedicated subagent_* event
// types for every phase), A2A dispatch/return reuse generic tool lifecycle
// events. Only intermediate progress is a dedicated a2a_call_progress event.
// All events are correlated by the same tool_call_id.
function isA2AToolEvent(event) {
  return (
    (event?.eventType === 'tool_start' || event?.eventType === 'tool_result' || event?.eventType === 'tool_error')
    && isA2AToolName(event?.metadata?.tool_name)
  );
}

function a2aCallItemId(toolCallId) {
  return `a2a-${toolCallId}`;
}

function createA2ACallItem(toolCallId, agentName, startedAt) {
  return {
    kind: 'a2a_call',
    id: a2aCallItemId(toolCallId),
    toolCallId,
    agentName: agentName || '',
    status: 'running',
    startedAt,
    finishedAt: null,
    durationMs: null,
    resultPreview: '',
    error: null,
    messages: [],
  };
}

function applyA2ACallEventToItem(item, nextEvent) {
  if (isTerminalLifecycleStatus(item.status)) {
    return item;
  }

  const metadata = nextEvent.metadata || {};
  const durationMs = Number.isFinite(metadata.duration_ms) ? metadata.duration_ms : null;

  switch (nextEvent.eventType) {
    case 'tool_start':
      return { ...item, status: 'running' };

    case 'a2a_call_progress':
      return {
        ...item,
        messages: [
          ...item.messages,
          {
            id: `${item.toolCallId}-${item.messages.length}`,
            state: typeof metadata.state === 'string' ? metadata.state : '',
            text: typeof metadata.message_text === 'string' ? metadata.message_text : (nextEvent.content || ''),
            timestamp: nextEvent.timestamp,
          },
        ],
      };

    case 'tool_result':
      return {
        ...item,
        status: 'success',
        finishedAt: nextEvent.timestamp ?? item.finishedAt,
        durationMs: durationMs ?? item.durationMs,
        resultPreview: typeof metadata.result_preview === 'string' ? metadata.result_preview : item.resultPreview,
      };

    case 'tool_error':
      return {
        ...item,
        status: 'error',
        finishedAt: nextEvent.timestamp ?? item.finishedAt,
        durationMs: durationMs ?? item.durationMs,
        error: typeof metadata.error === 'string' ? metadata.error : '',
      };

    default:
      return item;
  }
}

/**
 * Find-or-create-or-update the A2A call card for an A2A tool_start/
 * tool_result/tool_error or a2a_call_progress event, keyed by tool_call_id.
 * Mirrors applySubagentEvent's null-sentinel contract ("not mine, fall
 * through" vs. "handled, here's the updated array"). Unlike subagent's
 * defensive create-on-anything, an A2A tool result/error with no existing
 * card is ignored rather than guessed at -- a tool_start arrives first.
 */
export function applyA2ACallEvent(timelineItems, nextEvent) {
  if (!nextEvent) return null;
  const isProgressEvent = nextEvent.eventType === 'a2a_call_progress';
  if (!isA2AToolEvent(nextEvent) && !isProgressEvent) return null;

  const toolCallId = nextEvent.metadata?.tool_call_id;
  if (!toolCallId) return null;

  const itemId = a2aCallItemId(toolCallId);
  const index = timelineItems.findIndex((entry) => entry.kind === 'a2a_call' && entry.id === itemId);

  if (index === -1) {
    if (nextEvent.eventType !== 'tool_start') return null;
    const argsPreview = nextEvent.metadata?.args_preview || {};
    const agentName = typeof argsPreview.agent === 'string' ? argsPreview.agent : '';
    const seed = createA2ACallItem(toolCallId, agentName, nextEvent.timestamp);
    return [...timelineItems, applyA2ACallEventToItem(seed, nextEvent)];
  }

  return timelineItems.map((entry, idx) => (
    idx === index ? applyA2ACallEventToItem(entry, nextEvent) : entry
  ));
}

// Rows that never appear in the live preview (graph steps), or that providers
// deliver after the answer (the turn-wide reasoning summary), must not reopen
// the preview once answer text is streaming.
const PASSIVE_AFTER_TEXT_EVENT_TYPES = new Set(['node_update', 'reasoning_summary']);

/**
 * Apply a stream event. Any event that changes the visible timeline marks the
 * turn as working, except passive rows arriving after answer text started.
 */
export function applyAssistantStreamEvent(turn, streamEvent) {
  const { turn: base, streamEvent: event } = claimToolCommentary(turn, streamEvent);
  const next = applyStreamEventToTimeline(base, event);
  if (next === base || next.timelineItems === base.timelineItems) return next;
  if (turn.lastActivity === 'text' && PASSIVE_AFTER_TEXT_EVENT_TYPES.has(streamEvent?.event_type)) return next;
  return { ...next, lastActivity: 'work', pendingCommentary: '' };
}

// Moves the text streamed since the last work row off the answer and onto
// the tool call it introduced (the first, when calls run in parallel).
function claimToolCommentary(turn, streamEvent) {
  const commentary = turn.pendingCommentary || '';
  if (streamEvent?.event_type !== 'tool_start' || !commentary.trim()) return { turn, streamEvent };
  const content = turn.content.slice(0, turn.content.length - commentary.length);
  const timelineItems = turn.timelineItems.map((item) => (
    item.kind === 'assistant_message' && item.id === turn.assistantMessageId ? { ...item, content } : item
  ));
  return {
    turn: { ...turn, content, timelineItems, pendingCommentary: '' },
    streamEvent: { ...streamEvent, metadata: { ...(streamEvent.metadata || {}), commentary: commentary.trim() } },
  };
}

function applyStreamEventToTimeline(turn, streamEvent) {
  const nextEvent = addTaskSummaryMetadata(createStreamEvent(streamEvent));
  if (!nextEvent) return turn;

  // assistant_metadata is handled by applyAssistantMetadata, not here.
  // subagent_message_delta is NOT short-circuited here like message_delta is:
  // unlike the outer turn's text (redundantly available via message.content
  // after finalization), nested subagent text has no other persisted home.
  if (nextEvent.eventType === 'assistant_metadata' || nextEvent.eventType === 'message_delta') {
    return turn;
  }

  // usage_update is a terminal, non-visual metadata event (Azure's
  // prompt-cache-read token count for this turn) -- same treatment as
  // assistant_metadata: apply the value, never render its own timeline row.
  if (nextEvent.eventType === 'usage_update') {
    const cacheReadTokens = extractCacheReadTokenCount(nextEvent);
    const contextUsage = normalizeContextUsage(nextEvent.metadata?.context_usage);
    if (cacheReadTokens === null && !contextUsage) return turn;
    return {
      ...turn,
      ...(cacheReadTokens === null ? {} : { cacheReadTokenCount: cacheReadTokens }),
      ...(contextUsage ? { contextUsage } : {}),
    };
  }

  const next = { ...turn };
  const summaryAlreadyReceived = next.reasoningSummaryReceived
    || next.streamEvents.some((event) => event?.eventType === 'reasoning_summary');
  if (nextEvent.eventType === 'reasoning_delta' && summaryAlreadyReceived) {
    return next;
  }
  next.streamEvents = appendLiveStreamEvent(next.streamEvents, nextEvent);
  if (nextEvent.eventType === 'reasoning_summary') {
    next.reasoningSummaryReceived = true;
    next.reasoningText = nextEvent.content || next.reasoningText;
  } else {
    next.reasoningText = appendReasoningText(next.reasoningText, nextEvent);
  }
  const rt = extractReasoningTokenCount(nextEvent);
  if (rt !== null) next.reasoningTokenCount = rt;
  if (
    typeof nextEvent.eventType === 'string'
    && nextEvent.eventType.startsWith('subagent_')
    && !nextEvent.metadata?.tool_call_id
  ) {
    return next;
  }

  const updatedSubagentItems = applySubagentEvent(next.timelineItems, nextEvent);
  if (updatedSubagentItems) {
    next.timelineItems = updatedSubagentItems;
    return next;
  }

  // A2A tool lifecycle events update the rich a2a_call card but deliberately
  // also keep their plain stream_event row. The plain row is pushed first so
  // a freshly-seeded card follows its dispatch marker. Progress has no
  // generic tool-call equivalent, so it stays card-only.
  const isA2AToolEvt = isA2AToolEvent(nextEvent);
  if (isA2AToolEvt) {
    next.timelineItems = [...next.timelineItems, { kind: 'stream_event', id: nextEvent.id, event: nextEvent }];
  }
  const updatedA2ACallItems = applyA2ACallEvent(next.timelineItems, nextEvent);
  if (updatedA2ACallItems) {
    next.timelineItems = updatedA2ACallItems;
  }
  // A2A tool lifecycle events always got their plain row above regardless of
  // whether applyA2ACallEvent could build a card. Progress only short-circuits
  // when a card consumed it; an orphaned progress event falls through to the
  // generic stream_event push below.
  if (isA2AToolEvt || (nextEvent.eventType === 'a2a_call_progress' && updatedA2ACallItems)) {
    return next;
  }

  // One reasoning row per model call: a delta extends the trailing reasoning
  // row, while reasoning that follows any other row (tool call, plan update)
  // or answer text opens a new one -- that is the next model call. The
  // terminal summary repeats everything already streamed, so it only adds
  // its exact token count to a lone row, and only creates a row (ahead of
  // the work it led to) when the provider streamed no reasoning at all.
  const isReasoningKind = nextEvent.eventType === 'reasoning_delta' || nextEvent.eventType === 'reasoning_summary';
  if (isReasoningKind) {
    const reasoningIndexes = [];
    let lastWorkIndex = -1;
    next.timelineItems.forEach((entry, index) => {
      if (entry.kind === 'assistant_message') return;
      lastWorkIndex = index;
      if (entry.kind === 'stream_event' && isReasoningEvent(entry.event)) reasoningIndexes.push(index);
    });

    if (nextEvent.eventType === 'reasoning_summary') {
      if (reasoningIndexes.length > 1) return next;
      if (reasoningIndexes.length === 0) {
        next.timelineItems = [{ kind: 'stream_event', id: nextEvent.id, event: nextEvent }, ...next.timelineItems];
        return next;
      }
    }

    const targetIndex = nextEvent.eventType === 'reasoning_summary'
      ? reasoningIndexes[0]
      : (turn.lastActivity !== 'text' && reasoningIndexes.includes(lastWorkIndex) ? lastWorkIndex : -1);
    if (targetIndex >= 0) {
      const timelineItems = next.timelineItems.slice();
      const target = timelineItems[targetIndex];
      timelineItems[targetIndex] = { ...target, event: mergeReasoningEvents(target.event, nextEvent) };
      next.timelineItems = timelineItems;
    } else {
      next.timelineItems = [...next.timelineItems, { kind: 'stream_event', id: nextEvent.id, event: nextEvent }];
    }
    return next;
  }

  // todos_update carries the agent's FULL current plan snapshot on every call
  // (not a delta), but each call still gets its own timeline row -- the plan
  // can be many tool calls deep by the time it's next updated, so merging in
  // place would bury the current state under everything that ran since the
  // last update. A fresh row per update instead reads as plan progress
  // checkpoints threaded through the transcript. Falls through to the
  // generic append path below.

  if (isRenderableStreamEvent(nextEvent)) {
    next.timelineItems = [...next.timelineItems, { kind: 'stream_event', id: nextEvent.id, event: nextEvent }];
  }

  return next;
}

/** Apply a text chunk (stream_chunk event). */
export function applyAssistantChunk(turn, chunk, materialiseAt) {
  const next = { ...turn, lastActivity: 'text', pendingCommentary: (turn.pendingCommentary || '') + chunk };
  next.content += chunk;
  next.tokenCount += 1;

  // Materialise the assistant row on first text chunk.
  if (!next.assistantMessageId) {
    next.assistantMessageId = `${turn.streamId}-assistant`;
    next.assistantTimestamp = materialiseAt;
    next.timelineItems = [
      ...next.timelineItems,
      {
        kind: 'assistant_message',
        id: next.assistantMessageId,
        content: next.content,
        timestamp: next.assistantTimestamp,
        isStreaming: true,
        error: false,
        displayAgentName: next.displayAgentName || '',
        displayModelName: next.displayModelName,
        displayToolCalls: next.displayToolCalls,
        tokenCount: next.tokenCount,
        reasoningTokenCount: next.reasoningTokenCount,
        reasoningText: next.reasoningText,
      },
    ];
  } else {
    // Update existing assistant row in timelineItems without rebuilding every item.
    const timelineItems = next.timelineItems.slice();
    let assistantIndex = -1;
    for (let index = 0; index < timelineItems.length; index += 1) {
      const item = timelineItems[index];
      if (item.kind === 'assistant_message' && item.id === next.assistantMessageId) {
        assistantIndex = index;
        break;
      }
    }
    if (assistantIndex >= 0) {
      const item = timelineItems[assistantIndex];
      timelineItems[assistantIndex] = {
        ...item,
        content: next.content,
        tokenCount: next.tokenCount,
        reasoningTokenCount: next.reasoningTokenCount,
        reasoningText: next.reasoningText,
      };
    }
    next.timelineItems = timelineItems;
  }

  return next;
}

// The backend persists raw tool_start/tool_result events but not the
// synthetic todos_update events the live turn appends (see
// applyAssistantStreamEvent) -- there's no message backing a todos_update,
// it's a side-channel snapshot of graph state. Reconstruct one entry per
// write_todos call from its tool_start args (structured JSON, see
// build_tool_start_event on the backend), keyed by tool_call_id so each can
// be re-inserted next to its matching tool_result below.
function collectTodosByToolCallId(streamEvents) {
  const todosByCallId = new Map();
  for (const event of streamEvents) {
    if (event?.eventType !== 'tool_start' || event?.metadata?.tool_name !== 'write_todos') {
      continue;
    }
    const callId = event.metadata?.tool_call_id;
    const todos = event.metadata?.args_preview?.todos;
    if (callId && Array.isArray(todos)) {
      todosByCallId.set(callId, todos);
    }
  }
  return todosByCallId;
}

// Reconstructs grouped subagent cards from a flat, persisted stream_events
// array. Any event with a usable correlation id can seed a recoverable card;
// events without one cannot be associated with a subagent and are ignored.
function collectSubagentGroups(streamEvents) {
  let items = [];
  for (const event of streamEvents) {
    if (typeof event?.eventType !== 'string' || !event.eventType.startsWith('subagent_')) {
      continue;
    }
    const toolCallId = typeof event.metadata?.tool_call_id === 'string'
      ? event.metadata.tool_call_id.trim()
      : '';
    if (!toolCallId) continue;
    const updated = applySubagentEvent(items, event);
    if (updated) items = updated;
  }
  const groups = new Map();
  for (const item of items) {
    groups.set(item.toolCallId, item);
  }
  return groups;
}

// Reconstructs grouped A2A call cards from a flat, persisted stream_events
// array by replaying applyA2ACallEvent over the flat events in order so live
// and reload cannot drift apart.
function collectA2ACallGroups(streamEvents) {
  let items = [];
  for (const event of streamEvents) {
    if (!isA2AToolEvent(event) && event?.eventType !== 'a2a_call_progress') continue;
    const updated = applyA2ACallEvent(items, event);
    if (updated) items = updated;
  }
  const groups = new Map();
  for (const item of items) {
    groups.set(item.toolCallId, item);
  }
  return groups;
}

/**
 * Build a fallback timeline order for a committed assistant message that has
 * no recorded `timelineItems` (e.g. backend-loaded history).
 *
 * The backend attaches a turn's tool calls to whichever message carries the
 * final answer text, so the live interleave itself isn't recorded -- but
 * most items here carry a real timestamp (an AIMessage's
 * response_metadata.created_at: stamped onto each tool call by
 * _extract_raw_tool_calls on the backend, and onto the assistant row via
 * ChatMessage.created_at), so sorting on that reproduces true order. Older
 * persisted tool calls predating that stamping fall back to the assistant
 * row's own timestamp instead (see the sort below) rather than corrupting
 * the order with NaN.
 */
export function buildDefaultAssistantTimeline(message) {
  if (!message || message.role !== 'assistant') return undefined;
  const items = [];
  const streamEvents = Array.isArray(message.streamEvents) ? message.streamEvents : [];

  const todosByCallId = collectTodosByToolCallId(streamEvents);
  const subagentGroups = collectSubagentGroups(streamEvents);
  const a2aCallGroups = collectA2ACallGroups(streamEvents);
  const insertedSubagentIds = new Set();
  const insertedA2ACallIds = new Set();
  let todosSeq = 0;
  for (const event of streamEvents) {
    // Correlated subagent events are folded into one card, seeded by the
    // earliest recoverable event (not only subagent_start). Events missing a
    // usable correlation id are unrecoverable and remain omitted.
    if (typeof event?.eventType === 'string' && event.eventType.startsWith('subagent_')) {
      const toolCallId = typeof event.metadata?.tool_call_id === 'string'
        ? event.metadata.tool_call_id.trim()
        : '';
      const group = toolCallId ? subagentGroups.get(toolCallId) : undefined;
      if (group) {
        if (!insertedSubagentIds.has(group.id)) {
          insertedSubagentIds.add(group.id);
          items.push({ ...group, timestamp: event.timestamp });
        }
        continue;
      }
      if (!toolCallId) continue;
    }

    // The grouped A2A call card splices in once after the earliest lifecycle
    // event carrying its tool_call_id. Lifecycle events retain plain rows
    // alongside the richer card, while progress is consumed only when a
    // matching group exists.
    if (isA2AToolEvent(event) || event?.eventType === 'a2a_call_progress') {
      const toolCallId = event.metadata?.tool_call_id;
      const group = toolCallId ? a2aCallGroups.get(toolCallId) : undefined;
      if (group) {
        if (event.eventType === 'a2a_call_progress') {
          if (!insertedA2ACallIds.has(group.id)) {
            insertedA2ACallIds.add(group.id);
            items.push({ ...group, timestamp: event.timestamp });
          }
          continue;
        }
        items.push({ kind: 'stream_event', id: event.id, event, timestamp: event.timestamp });
        if (!insertedA2ACallIds.has(group.id)) {
          insertedA2ACallIds.add(group.id);
          items.push({ ...group, timestamp: event.timestamp });
        }
        continue;
      }
    }

    if (isRenderableStreamEvent(event)) {
      items.push({ kind: 'stream_event', id: event.id, event, timestamp: event.timestamp });
    }

    if (event?.eventType !== 'tool_result' || event?.metadata?.tool_name !== 'write_todos') {
      continue;
    }
    const todos = todosByCallId.get(event.metadata?.tool_call_id);
    if (!todos) continue;

    todosSeq += 1;
    const restoredId = `${message.id || 'msg'}-todos-restored-${todosSeq}`;
    items.push({
      kind: 'stream_event',
      id: restoredId,
      timestamp: event.timestamp,
      event: {
        id: restoredId,
        eventType: 'todos_update',
        content: '',
        timestamp: event.timestamp,
        metadata: { todos },
      },
    });
  }

  items.push({
    kind: 'assistant_message',
    id: message.id || `msg_${Date.now()}`,
    content: typeof message.content === 'string' ? message.content : '',
    timestamp: message.timestamp,
    isStreaming: !!message.isStreaming,
    error: !!message.error,
    displayAgentName: typeof message.displayAgentName === 'string' ? message.displayAgentName : '',
    displayModelName: typeof message.displayModelName === 'string' ? message.displayModelName : '',
    displayToolCalls: Array.isArray(message.displayToolCalls) ? message.displayToolCalls : [],
    tokenCount: Number.isFinite(message.tokenCount) ? message.tokenCount : 0,
    reasoningTokenCount: Number.isFinite(message.reasoningTokenCount) ? message.reasoningTokenCount : 0,
    reasoningText: typeof message.reasoningText === 'string' ? message.reasoningText : '',
  });

  // Stream events reconstructed from pre-timestamp checkpoint data (or any
  // malformed persisted event) can carry an undefined timestamp -- subtracting
  // undefined produces NaN, which Array.sort's comparator contract treats as
  // "equal" inconsistently across engines. Fall back to the message's own
  // timestamp (always valid, see normalizeBackendChatMessage) so those items
  // sort next to the turn they belong to instead of corrupting the order.
  const fallbackTimestamp = Number.isFinite(message.timestamp) ? message.timestamp : 0;
  return items.sort((a, b) => {
    const at = Number.isFinite(a.timestamp) ? a.timestamp : fallbackTimestamp;
    const bt = Number.isFinite(b.timestamp) ? b.timestamp : fallbackTimestamp;
    return at - bt;
  });
}

/**
 * Convert a live turn to a message-like object suitable for rendering with
 * the `<Message>` component. Only meaningful once the turn has an assistant
 * text row or has been finalised.
 */
export function toCommittedAssistantMessage(turn) {
  return {
    streamId: turn.streamId,
    assistantMessageId: turn.assistantMessageId || `${turn.streamId}-assistant`,
    startedAt: turn.startedAt,
    id: turn.assistantMessageId || `${turn.streamId}-assistant`,
    role: 'assistant',
    content: turn.content,
    timestamp: turn.assistantTimestamp || turn.startedAt,
    responseTimestamp: turn.responseTimestamp ?? null,
    tokenCount: turn.tokenCount,
    reasoningTokenCount: turn.reasoningTokenCount,
    cacheReadTokenCount: turn.cacheReadTokenCount,
    contextUsage: turn.contextUsage,
    reasoningText: turn.reasoningText,
    streamEvents: turn.streamEvents,
    displayAgentName: turn.displayAgentName,
    displayModelName: turn.displayModelName,
    displayToolCalls: turn.displayToolCalls,
    isStreaming: turn.isStreaming,
    error: turn.error,
    workActive: Boolean(turn.isStreaming) && turn.lastActivity === 'work',
    timelineItems: turn.timelineItems,
  };
}

/**
 * Finalise a live assistant turn — freeze it and return a committed message.
 *
 * Rules:
 * - `completed` with visible output → normal message.
 * - `completed` with **no** visible output → "(No response text)" placeholder.
 * - `cancelled` with no visible output → drop (return null).
 * - `cancelled` with partial visible output → keep, append cancelled suffix once.
 * - `error` → message with `Error: ...` content and `error: true`.
 */
export function finalizeLiveAssistantTurn(turn, { status, now, errorMessage }) {
  if (!turn) return null;

  const hasVisibleOutput = (
    turn.content.trim().length > 0
    || hasVisibleTimelineOutput(turn.timelineItems)
  );

  if (status === 'cancelled' && !hasVisibleOutput) {
    return null;
  }

  // Matches history normalization: only a completed reply with answer text
  // starts the cache countdown; tool-only or empty turns never do.
  const hasAnswerText = typeof turn.content === 'string' && turn.content.trim().length > 0;
  const finalTurn = {
    ...turn, isStreaming: false, assistantTimestamp: now || turn.startedAt,
    responseTimestamp: status === 'completed' && hasAnswerText ? now || turn.startedAt : null,
  };

  if (status === 'error') {
    const errorContent = `Error: ${errorMessage || 'Unknown error'}`;
    const priorContent = typeof turn.content === 'string' ? turn.content : '';
    finalTurn.content = errorContent;
    finalTurn.error = true;

    // Keep every event/card and any partial assistant text that was already
    // rendered. The error is an additional terminal row, not a replacement
    // for the trace that explains what failed.
    const timelineItems = (Array.isArray(finalTurn.timelineItems) ? finalTurn.timelineItems : [])
      .map((item) => item.kind === 'assistant_message'
        ? {
          ...item,
          isStreaming: false,
          tokenCount: finalTurn.tokenCount,
          reasoningTokenCount: finalTurn.reasoningTokenCount,
          reasoningText: finalTurn.reasoningText,
        }
        : item);

    if (!finalTurn.assistantMessageId) {
      finalTurn.assistantMessageId = `${finalTurn.streamId}-assistant`;
    }
    const hasAssistantItem = timelineItems.some((item) => item.kind === 'assistant_message');
    if (!hasAssistantItem && priorContent.trim().length > 0) {
      timelineItems.push({
        kind: 'assistant_message',
        id: finalTurn.assistantMessageId,
        content: priorContent,
        timestamp: turn.assistantTimestamp || finalTurn.assistantTimestamp,
        isStreaming: false,
        error: false,
        displayAgentName: finalTurn.displayAgentName || '',
        displayModelName: finalTurn.displayModelName,
        displayToolCalls: finalTurn.displayToolCalls,
        tokenCount: finalTurn.tokenCount,
        reasoningTokenCount: finalTurn.reasoningTokenCount,
        reasoningText: finalTurn.reasoningText,
      });
    }

    timelineItems.push({
      kind: 'assistant_message',
      id: `${finalTurn.assistantMessageId}-error`,
      content: errorContent,
      timestamp: finalTurn.assistantTimestamp,
      isStreaming: false,
      error: true,
      displayAgentName: finalTurn.displayAgentName || '',
      displayModelName: finalTurn.displayModelName,
      displayToolCalls: finalTurn.displayToolCalls,
      tokenCount: finalTurn.tokenCount,
      reasoningTokenCount: finalTurn.reasoningTokenCount,
      reasoningText: finalTurn.reasoningText,
    });
    finalTurn.timelineItems = timelineItems;
    return toCommittedAssistantMessage(finalTurn);
  }

  if (status === 'completed' && !hasVisibleOutput) {
    // No text and no visible events — show placeholder so the cursor doesn't vanish.
    finalTurn.content = '(No response text)';
    if (!finalTurn.assistantMessageId) {
      finalTurn.assistantMessageId = `${finalTurn.streamId}-assistant`;
    }
    finalTurn.timelineItems = [
      {
        kind: 'assistant_message',
        id: finalTurn.assistantMessageId,
        content: finalTurn.content,
        timestamp: finalTurn.assistantTimestamp,
        isStreaming: false,
        error: false,
        displayAgentName: finalTurn.displayAgentName || '',
        displayModelName: finalTurn.displayModelName,
        displayToolCalls: finalTurn.displayToolCalls,
        tokenCount: finalTurn.tokenCount,
        reasoningTokenCount: finalTurn.reasoningTokenCount,
        reasoningText: finalTurn.reasoningText,
      },
    ];
    return toCommittedAssistantMessage(finalTurn);
  }

  if (status === 'cancelled' && hasVisibleOutput) {
    finalTurn.content = finalTurn.content.endsWith(' [Cancelled]')
      ? finalTurn.content
      : `${finalTurn.content} [Cancelled]`;
  }

  // Ensure the timeline items reflect the final state.
  finalTurn.timelineItems = finalTurn.timelineItems.map((item) => {
    if (item.kind === 'assistant_message') {
      return {
        ...item,
        isStreaming: false,
        content: finalTurn.content,
        tokenCount: finalTurn.tokenCount,
        reasoningTokenCount: finalTurn.reasoningTokenCount,
        reasoningText: finalTurn.reasoningText,
      };
    }
    return item;
  });

  return toCommittedAssistantMessage(finalTurn);
}
